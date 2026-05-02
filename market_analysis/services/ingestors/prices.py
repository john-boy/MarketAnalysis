"""Price ingestor.

Fetches daily OHLCV for a symbol via the Alpha Vantage adapter and
writes it to the ``daily_quotes`` time-series collection.

Two modes:

- ``"auto"`` (default, daily operations): look up the max ``date`` for
  ``symbol`` in ``daily_quotes``.  If quotes exist, pull AV's 100-bar
  ``compact`` window and insert only rows with ``date > last_date`` —
  no deletes.  If the compact payload's oldest date is still later
  than ``last_date + 1`` (a >100-trading-day gap), escalate to a full
  refresh.  If no quotes exist, pull the full 20+-year history.
- ``"full"`` (initial load / repair): ``delete_many({metadata.symbol})``
  then insert the full payload.

Time-series collections don't support unique indexes, so incremental
mode relies on the in-memory "date > last" filter; full mode relies
on the delete-first-insert-later ordering.  Both are idempotent.

This module has **no Qt imports**; it is callable from the poller
and from CLI scripts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from market_analysis.data import mongo
from market_analysis.services.ingestors._common import is_tradeable_symbol
from market_analysis.sources.alpha_vantage import AlphaVantageClient


log = logging.getLogger(__name__)


# -- AV payload normalization --------------------------------------------


# Alpha Vantage ``TIME_SERIES_DAILY_ADJUSTED`` field mapping.
_AV_FIELDS = {
    "1. open":               ("open",              float),
    "2. high":               ("high",              float),
    "3. low":                ("low",               float),
    "4. close":              ("close",             float),
    "5. adjusted close":     ("adjusted_close",    float),
    "6. volume":             ("volume",            float),
    "7. dividend amount":    ("dividend",          float),
    "8. split coefficient":  ("split_coefficient", float),
}


def _extract_daily_series(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the inner time-series dict from an AV daily payload.

    AV uses a few different wrapper keys across endpoints (``Time Series
    (Daily)`` for TIME_SERIES_DAILY_ADJUSTED and INDEX_DATA; ``data``
    occasionally).  This helper finds whichever one is present.
    """
    for key in ("Time Series (Daily)", "data", "Time Series (Index)"):
        v = payload.get(key)
        if isinstance(v, dict) and v:
            return v
    return {}


def _parse_daily_adjusted(
    payload: dict[str, Any], symbol: str, source: str = "alpha_vantage",
) -> list[dict[str, Any]]:
    """Translate an AV payload into ``daily_quotes`` docs."""
    series = payload.get("Time Series (Daily)") or {}
    if not isinstance(series, dict) or not series:
        return []

    docs: list[dict[str, Any]] = []
    for date_str, row in series.items():
        date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        doc: dict[str, Any] = {
            "date": date,
            "metadata": {"symbol": symbol, "source": source},
        }
        for av_key, (target_key, caster) in _AV_FIELDS.items():
            raw = row.get(av_key)
            if raw is None:
                continue
            try:
                doc[target_key] = caster(raw)
            except (TypeError, ValueError):
                continue
        # candle (prototype parity): close - open
        if "open" in doc and "close" in doc:
            doc["candle"] = doc["close"] - doc["open"]
        docs.append(doc)

    docs.sort(key=lambda d: d["date"])
    return docs


# Alpha Vantage ``INDEX_DATA`` field mapping.  Unlike the equities
# ``TIME_SERIES_*`` endpoints, INDEX_DATA returns a *list* of records
# under a top-level ``data`` key, with bare field names (no "1. open"
# numeric prefix).  OHLC only — no volume / dividend / adjusted-close.
_AV_INDEX_FIELDS = {
    "open":   ("open",   float),
    "high":   ("high",   float),
    "low":    ("low",    float),
    "close":  ("close",  float),
    "volume": ("volume", float),  # tolerated if AV starts emitting it
}


def _parse_index_daily(
    payload: dict[str, Any], symbol: str, source: str = "alpha_vantage",
) -> list[dict[str, Any]]:
    """Translate an AV ``INDEX_DATA`` payload into ``daily_quotes`` docs.

    Accepts both AV INDEX_DATA shapes:

    * Current production shape::

        {"symbol": "VIX", "interval": "daily",
         "data": [{"date": "2026-04-23", "open": "19.42", ...}, ...]}

    * Legacy/compat shape (Time Series keyed by date) — kept so
      cassettes/tests written against the older format still parse.

    Rows end up identical to :func:`_parse_daily_adjusted` output so
    index quotes sit side by side with equity quotes in ``daily_quotes``.
    """
    rows: list[dict[str, Any]] = []
    data = payload.get("data")
    if isinstance(data, list) and data:
        rows = [r for r in data if isinstance(r, dict)]
    else:
        # Legacy: date-keyed dict.  Convert to the list shape.
        series = _extract_daily_series(payload)
        for date_str, row in series.items():
            if isinstance(row, dict):
                merged = {"date": date_str}
                # Map "1. open" → "open", etc.
                for k, v in row.items():
                    parts = k.split(". ", 1)
                    merged[parts[-1]] = v
                rows.append(merged)

    docs: list[dict[str, Any]] = []
    for row in rows:
        date_str = row.get("date")
        if not isinstance(date_str, str):
            continue
        try:
            date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        doc: dict[str, Any] = {
            "date": date,
            "metadata": {"symbol": symbol, "source": source},
        }
        for av_key, (target_key, caster) in _AV_INDEX_FIELDS.items():
            raw = row.get(av_key)
            if raw is None:
                continue
            try:
                doc[target_key] = caster(raw)
            except (TypeError, ValueError):
                continue
        if "open" in doc and "close" in doc:
            doc["candle"] = doc["close"] - doc["open"]
        docs.append(doc)

    docs.sort(key=lambda d: d["date"])
    return docs


# -- Public API -----------------------------------------------------------


@dataclass
class PriceIngestReport:
    symbol: str
    mode: str = "auto"       # "auto" resolves to "incremental" | "full-bootstrap"
    inserted: int = 0
    deleted: int = 0
    first_date: datetime | None = None
    last_date: datetime | None = None
    escalated: bool = False  # incremental → full because of gap
    empty_payload: bool = False  # AV returned no rows (silent miss)


def _last_stored_date(symbol: str) -> datetime | None:
    doc = mongo.daily_quotes().find_one(
        {"metadata.symbol": symbol},
        sort=[("date", -1)],
        projection={"date": 1, "_id": 0},
    )
    if not doc:
        return None
    # BSON datetimes decode as naive UTC by default; AV-parsed dates are
    # tz-aware UTC. Normalize so downstream comparisons don't mix kinds.
    d = doc["date"]
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def ingest_prices(
    symbol: str,
    *,
    client: AlphaVantageClient | None = None,
    mode: str = "auto",
) -> PriceIngestReport:
    """Fetch and store ``symbol``'s daily adjusted series.

    ``mode`` selects the strategy:

    - ``"auto"`` — incremental if existing quotes, full if not.  Daily
      operations should use this.
    - ``"full"`` — force a delete-all-and-backfill.  Use for initial
      loading or repair.
    """
    if not is_tradeable_symbol(symbol):
        # Guard against cash / N.A. placeholders sneaking into callers
        # that didn't pre-filter.  AV returns an error envelope for these
        # and poisons the daily run with noise.
        log.debug("Skipping non-tradeable symbol %r", symbol)
        return PriceIngestReport(symbol=symbol, mode=mode)

    av = client or AlphaVantageClient()

    if mode == "full":
        return _ingest_full(symbol, av)
    if mode == "auto":
        last = _last_stored_date(symbol)
        if last is None:
            return _ingest_full(symbol, av)
        return _ingest_incremental(symbol, av, last)
    raise ValueError(f"Unknown ingest mode: {mode!r}")


def _ingest_full(symbol: str, av: AlphaVantageClient) -> PriceIngestReport:
    payload = av.time_series_daily_adjusted(symbol, outputsize="full")
    docs = _parse_daily_adjusted(payload, symbol)

    coll = mongo.daily_quotes()
    deleted = coll.delete_many({"metadata.symbol": symbol}).deleted_count
    if not docs:
        log.warning("No daily series for %s; wrote nothing.", symbol)
        return PriceIngestReport(symbol=symbol, mode="full", deleted=deleted)

    coll.insert_many(docs, ordered=False)
    return PriceIngestReport(
        symbol=symbol,
        mode="full",
        inserted=len(docs),
        deleted=deleted,
        first_date=docs[0]["date"],
        last_date=docs[-1]["date"],
    )


def _ingest_incremental(
    symbol: str, av: AlphaVantageClient, last: datetime,
) -> PriceIngestReport:
    payload = av.time_series_daily_adjusted(symbol, outputsize="compact")
    docs = _parse_daily_adjusted(payload, symbol)
    if not docs:
        log.warning("No compact series for %s; wrote nothing.", symbol)
        return PriceIngestReport(
            symbol=symbol, mode="incremental", empty_payload=True,
        )

    # Gap check: if the oldest bar in the compact window is still newer
    # than the last stored bar, a gap > 100 trading days exists.  Fall
    # back to a full pull so we don't leave a hole.
    if docs[0]["date"] > last:
        log.warning(
            "%s: gap > compact window (last=%s, oldest new=%s); "
            "escalating to full refresh.",
            symbol, last.date(), docs[0]["date"].date(),
        )
        rep = _ingest_full(symbol, av)
        rep.escalated = True
        rep.mode = "full-escalated"
        return rep

    new_docs = [d for d in docs if d["date"] > last]
    if not new_docs:
        return PriceIngestReport(
            symbol=symbol, mode="incremental",
            first_date=last, last_date=last,
        )

    mongo.daily_quotes().insert_many(new_docs, ordered=False)
    return PriceIngestReport(
        symbol=symbol,
        mode="incremental",
        inserted=len(new_docs),
        first_date=new_docs[0]["date"],
        last_date=new_docs[-1]["date"],
    )
