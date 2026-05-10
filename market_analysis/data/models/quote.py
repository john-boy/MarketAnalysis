"""Daily quote model — time-series ``price_history`` (WyckoffDB).

Doc shape: raw OHLCV from AV + adjusted OHLCV computed at ingest +
a ``metadata`` subdocument (the collection's metaField, see ADR-0008
and docs/WYCKOFF_CODE_SPEC.md)::

    {
        "date":   <datetime>,
        "metadata": {"symbol": "AMZN", "source": "alpha_vantage",
                     "asset_type": "equity"},
        "open": 68.06, "high": 71.88, "low": 66.31, "close": 69.13,
        "volume": 12824100.0,
        "adjusted_close": 3.4565, "dividend": 0.0, "split_coefficient": 1.0,
        "adj_factor": 0.0507, "adj_open": 3.45, "adj_high": 3.65,
        "adj_low": 3.36,  "adj_close": 3.4565, "adj_volume": 252837253,
        "candle": 1.07,   "adj_candle": 0.054,
    }
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from market_analysis.data.models._base import TimeseriesModel


class QuoteMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    source: str | None = None         # e.g. "alpha_vantage", "schwab"
    asset_type: str | None = None     # "equity" | "etf" | "index"


class Quote(TimeseriesModel):
    metadata: QuoteMeta

    # Raw OHLCV from AV
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adjusted_close: float | None = None
    volume: float | None = None
    dividend: float | None = None
    split_coefficient: float | None = None

    # Split + dividend adjusted, computed at ingest (WyckoffDB)
    adj_factor: float | None = None
    adj_open: float | None = None
    adj_high: float | None = None
    adj_low: float | None = None
    adj_close: float | None = None
    adj_volume: int | None = None

    # Pre-computed body sizes
    candle: float | None = None
    adj_candle: float | None = None
