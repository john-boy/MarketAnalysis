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
