"""Cross-ingestor helpers.

Tiny module — home for things every ingestor needs but that don't
deserve their own file.  Currently:

- :func:`is_tradeable_symbol`: filter out cash / dollar sentinels
  ETF_PROFILE sometimes emits as a "holding".  AV has no price series
  for these, so calling `time_series_daily_adjusted("n/a")` returns an
  error envelope and poisons the daily run with noise.
"""

from __future__ import annotations


# Sentinels ETF providers use for non-tradeable holdings (cash buffers,
# margin balances, other-fund buckets).  Case-folded for comparison.
_NON_TRADEABLE_SYMBOLS = frozenset({
    "N/A",
    "CASH",
    "USD",
    "OTHER",
    "UNKNOWN",
    "",
    "-",
})


def is_tradeable_symbol(symbol: str | None) -> bool:
    """True when ``symbol`` looks like a real ticker we can fetch.

    Non-tradeable holdings (``n/a``, ``Cash``, ``USD``, …) are filtered
    out so downstream AV calls don't waste budget on guaranteed misses.
    """
    if not symbol:
        return False
    return symbol.strip().upper() not in _NON_TRADEABLE_SYMBOLS


def derive_asset_type(symbol: str) -> str:
    """Return ``"etf"``, ``"index"``, or ``"equity"`` for ``symbol``.

    Decision order (per WYCKOFF_CODE_SPEC.md Change 4):
      1. Symbol present in the ``etf`` collection      -> "etf"
      2. Watchlist entry with ``ingest_tags`` "index"  -> "index"
      3. Tracked in the ``indexes`` collection         -> "index"
      4. Otherwise                                     -> "equity"

    Looked up at call time so newly-added watchlist tags or ETFs are
    picked up without a restart.
    """
    from market_analysis.data import mongo

    sym = (symbol or "").upper()
    if mongo.etf().count_documents({"symbol": sym}, limit=1):
        return "etf"
    wl = mongo.watchlist().find_one(
        {"symbol": sym}, {"ingest_tags": 1, "_id": 0}
    )
    if wl and "index" in {t.lower() for t in (wl.get("ingest_tags") or [])}:
        return "index"
    if mongo.indexes().count_documents({"symbol": sym}, limit=1):
        return "index"
    return "equity"


def compute_adj_fields(doc: dict) -> dict:
    """Compute split/dividend-adjusted OHLCV fields for one quote doc.

    Returns a dict of fields to merge into ``doc`` immediately before
    insertion into ``WyckoffDB.price_history``. This is the ONLY place
    the adjustment math lives — both the historical migration and the
    daily ingestion path call it. See WYCKOFF_CODE_SPEC.md § "Changes
    to the existing ingestion program — Change 3".

    ``adj_factor = adjusted_close / close``: 1.0 on non-split / non-
    dividend days, < 1.0 on pre-split history (raw prices were higher).
    Adjusted prices scale by the factor; volume scales inversely.

    Edge cases:
      - close == 0 or adjusted_close missing -> factor = 1.0
      - For indices (no volume, no adjusted_close): factor = 1.0,
        adj_* = raw values where present, None otherwise.
    """
    close = doc.get("close") or 0.0
    adj_cl = doc.get("adjusted_close")
    if adj_cl is None:
        adj_cl = close
    factor = (adj_cl / close) if close else 1.0

    def _ap(v: float | None) -> float | None:
        return round(v * factor, 6) if v is not None else None

    def _av(v: float | None) -> int | None:
        return round(v / factor) if v is not None and factor else None

    raw_open = doc.get("open")
    return {
        "adj_factor": round(factor, 8),
        "adj_open": _ap(raw_open),
        "adj_high": _ap(doc.get("high")),
        "adj_low": _ap(doc.get("low")),
        "adj_close": round(float(adj_cl), 6) if adj_cl is not None else None,
        "adj_volume": _av(doc.get("volume")),
        "candle": round(float(close) - float(raw_open or 0), 6) if raw_open is not None else None,
        "adj_candle": round(float(adj_cl) - float(_ap(raw_open) or 0), 6) if raw_open is not None and adj_cl is not None else None,
    }
