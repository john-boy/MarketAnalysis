"""Index ingestor.

Fetches and stores price data for a tracked market index.  Dispatches
on the index's mode (see :class:`MarketIndex`):

- **Proxy mode** (SPX → SPY).  Price data lives under the proxy ETF's
  symbol and is ingested by the normal ETF/price pipeline.  Here we
  only verify the proxy has data and stamp ``last_ingested`` so the
  Admin UI can surface the relationship honestly.
- **Direct mode** (VIX).  Fetch Alpha Vantage under ``fetch_symbol``;
  store under the index's own ``symbol`` in ``daily_quotes``.  Reuses
  the price ingestor's auto/full incremental logic.

Errors are captured on the index record (``last_error``) so the UI
can show a per-row status without crashing the daily run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from market_analysis.data import mongo
from market_analysis.data.models import MarketIndex, utcnow
from market_analysis.services.ingestors import prices as price_ingestor
from market_analysis.sources.alpha_vantage import AlphaVantageClient, AlphaVantageError


log = logging.getLogger(__name__)


@dataclass
class IndexIngestReport:
    symbol: str
    mode: Literal["proxy", "direct"]
    # Proxy-mode specifics:
    proxy_symbol: str | None = None
    proxy_last_date: datetime | None = None
    # Direct-mode specifics:
    inserted: int = 0
    escalated: bool = False
    first_date: datetime | None = None
    last_date: datetime | None = None
    # Error, if any — recorded both here and on the index record.
    error: str | None = None


def _load_index(symbol: str) -> MarketIndex | None:
    doc = mongo.indexes().find_one({"symbol": symbol.upper()})
    if not doc:
        return None
    return MarketIndex.model_validate(doc)


def _stamp(symbol: str, *, ok: bool, error: str | None = None) -> None:
    """Record the operational outcome on the index document."""
    now = utcnow()
    update: dict = {"last_updated": now}
    if ok:
        update["last_ingested"] = now
        update["last_error"] = None
    elif error is not None:
        update["last_error"] = error
    mongo.indexes().update_one({"symbol": symbol}, {"$set": update})


def ingest_index(
    symbol: str,
    *,
    client: AlphaVantageClient | None = None,
    mode: str = "auto",
) -> IndexIngestReport:
    """Ingest a single tracked index by its canonical symbol.

    ``mode`` is forwarded to the direct-mode price path (``"auto"`` or
    ``"full"``); it has no effect in proxy mode, which only verifies
    the proxy's data.  Raises ``KeyError`` if the index isn't tracked.
    """
    sym = symbol.upper()
    rec = _load_index(sym)
    if rec is None:
        raise KeyError(f"Index not tracked: {sym!r}")

    if rec.mode == "proxy":
        proxy = rec.proxy_symbol
        assert proxy is not None  # invariant: enforced by the model validator
        proxy_last = mongo.daily_quotes().find_one(
            {"metadata.symbol": proxy},
            sort=[("date", -1)],
            projection={"date": 1, "_id": 0},
        )
        if proxy_last is None:
            err = (
                f"proxy {proxy!r} has no price history yet; ingest it first "
                f"(e.g. via an ETF refresh)"
            )
            _stamp(sym, ok=False, error=err)
            return IndexIngestReport(
                symbol=sym, mode="proxy", proxy_symbol=proxy, error=err,
            )
        _stamp(sym, ok=True)
        return IndexIngestReport(
            symbol=sym,
            mode="proxy",
            proxy_symbol=proxy,
            proxy_last_date=proxy_last["date"],
        )

    # direct mode
    fetch_sym = rec.fetch_symbol
    assert fetch_sym is not None  # invariant
    av = client or AlphaVantageClient()
    try:
        # Indexes use the dedicated AV ``INDEX_DATA`` endpoint — no
        # adjusted close, no compact window, no splits/dividends.  We
        # always route through the alias helper so the logic stays in
        # one place, whether or not ``fetch_symbol`` differs from the
        # canonical symbol.
        pr = _ingest_direct_with_alias(sym, fetch_sym, av, mode)
    except AlphaVantageError as e:
        err = f"AV error: {e}"
        _stamp(sym, ok=False, error=err)
        return IndexIngestReport(symbol=sym, mode="direct", error=err)
    except Exception as e:  # noqa: BLE001
        err = f"unexpected error: {e}"
        log.exception("Index ingest failed for %s", sym)
        _stamp(sym, ok=False, error=err)
        return IndexIngestReport(symbol=sym, mode="direct", error=err)

    _stamp(sym, ok=True)
    return IndexIngestReport(
        symbol=sym,
        mode="direct",
        inserted=pr.inserted,
        escalated=pr.escalated,
        first_date=pr.first_date,
        last_date=pr.last_date,
    )


def _ingest_direct_with_alias(
    canonical: str,
    fetch_sym: str,
    av: AlphaVantageClient,
    mode: str,
) -> price_ingestor.PriceIngestReport:
    """Direct-mode ingest via AV ``INDEX_DATA``.

    Always routes through INDEX_DATA (not ``TIME_SERIES_DAILY_ADJUSTED``):
    indexes aren't adjusted or dividend-paying, and the dedicated
    endpoint is what AV documents for SPX / NDX / VIX etc.  Rows are
    persisted under ``canonical`` even when ``fetch_sym`` differs.

    ``mode`` is either ``"full"`` (delete + refetch) or ``"auto"``
    (incremental with gap-escalation, mirroring the equity price path).
    INDEX_DATA returns the full history per call — there is no
    ``compact`` window — so we parse once and filter in-memory.
    """
    from market_analysis.services.ingestors.prices import (
        _last_stored_date,
        _parse_index_daily,
    )
    coll = mongo.daily_quotes()
    last = _last_stored_date(canonical)

    payload = av.index_data(fetch_sym, interval="daily")
    docs = _parse_index_daily(payload, canonical)

    if mode == "full" or last is None:
        deleted = coll.delete_many({"metadata.symbol": canonical}).deleted_count
        if docs:
            coll.insert_many(docs, ordered=False)
        return price_ingestor.PriceIngestReport(
            symbol=canonical,
            mode="full" if mode == "full" else "full-bootstrap",
            inserted=len(docs),
            deleted=deleted,
            first_date=docs[0]["date"] if docs else None,
            last_date=docs[-1]["date"] if docs else None,
        )

    # incremental
    new_docs = [d for d in docs if d["date"] > last]
    if new_docs:
        coll.insert_many(new_docs, ordered=False)
    return price_ingestor.PriceIngestReport(
        symbol=canonical,
        mode="incremental",
        inserted=len(new_docs),
        first_date=new_docs[0]["date"] if new_docs else None,
        last_date=new_docs[-1]["date"] if new_docs else None,
    )


def list_tracked() -> list[MarketIndex]:
    """Return every tracked index, sorted by symbol."""
    rows = mongo.indexes().find({}).sort("symbol", 1)
    return [MarketIndex.model_validate(r) for r in rows]


def add_index(idx: MarketIndex) -> bool:
    """Insert a new tracked index.  Returns True if inserted, False if it exists."""
    coll = mongo.indexes()
    res = coll.update_one(
        {"symbol": idx.symbol},
        {"$setOnInsert": idx.to_mongo()},
        upsert=True,
    )
    return res.upserted_id is not None


def remove_index(symbol: str) -> bool:
    """Remove a tracked index.  Returns True if a record was deleted.

    Does **not** delete any `daily_quotes` data written under the
    index's symbol (direct-mode indexes); that history is preserved
    unless the user explicitly cleans it up.
    """
    res = mongo.indexes().delete_one({"symbol": symbol.upper()})
    return res.deleted_count > 0
