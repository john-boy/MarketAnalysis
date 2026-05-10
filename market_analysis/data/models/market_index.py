"""Market index model — one doc per tracked index in ``indexes``.

A market index is a non-tradeable reference series (SPX, NDX, VIX,
DJI, etc.).  We track it in one of two modes:

- **Proxy mode** — the index is analytically equivalent to an ETF we
  already ingest (SPX ↔ SPY, NDX ↔ QQQ).  ``proxy_symbol`` names the
  ETF; the index record carries no price history of its own, and
  downstream readers resolve prices via the proxy.  Avoids duplicate
  time-series storage.
- **Direct mode** — the index has its own price series fetched from
  the data provider (VIX).  ``fetch_symbol`` is the provider-specific
  identifier; ``price_history`` stores rows under the index's own
  symbol.

``proxy_symbol`` and ``fetch_symbol`` are mutually exclusive: set one
or the other, never both, never neither.  The mode is derived from
which field is populated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from market_analysis.data.models._base import MongoModel, utcnow


IndexCategory = Literal[
    "broad_market",     # SPX, NDX, DJI
    "volatility",       # VIX, VXN
    "sector",           # XLK, XLE, ...  (when the ETF is really an index wrapper)
    "international",    # FTSE, NIKKEI
    "fixed_income",     # bond indexes
    "commodity",        # GSCI, BCOM
    "custom",           # user-defined
]


class MarketIndex(MongoModel):
    symbol: str                                # canonical index symbol, e.g. "SPX"
    name: str | None = None                    # human-readable name
    category: IndexCategory = "broad_market"

    # Mutually exclusive: exactly one must be set.
    proxy_symbol: str | None = None            # ETF symbol whose prices stand in
    fetch_symbol: str | None = None            # provider-specific ticker for direct fetch

    description: str | None = None
    notes: str | None = None

    # Populated by the ingest worker; tracks operational state.
    last_ingested: datetime | None = None
    last_error: str | None = None

    added_at: datetime = Field(default_factory=utcnow)
    last_updated: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _exactly_one_mode(self):
        has_proxy = bool(self.proxy_symbol)
        has_fetch = bool(self.fetch_symbol)
        if has_proxy == has_fetch:  # both set, or neither set
            raise ValueError(
                "MarketIndex requires exactly one of proxy_symbol or fetch_symbol; "
                f"got proxy_symbol={self.proxy_symbol!r}, fetch_symbol={self.fetch_symbol!r}"
            )
        return self

    @property
    def mode(self) -> Literal["proxy", "direct"]:
        return "proxy" if self.proxy_symbol else "direct"

    @property
    def price_source_symbol(self) -> str:
        """Symbol under which this index's price data is *stored* in price_history.

        - Proxy mode: the proxy ETF's symbol (SPY for SPX).
        - Direct mode: the index's own symbol (VIX for VIX).
        """
        return self.proxy_symbol if self.proxy_symbol else self.symbol
