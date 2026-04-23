"""Daily quote model — time-series ``daily_quotes``.

Doc shape mirrors the prototype: top-level OHLCV + a ``metadata``
subdocument (the collection's metaField, see ADR-0008)::

    {
        "date":   <datetime>,
        "metadata": {"symbol": "AMZN", "source": "alpha_vantage"},
        "open": 68.06, "high": 71.88, "low": 66.31, "close": 69.13,
        "adjusted_close": 3.4565, "volume": 12824100.0,
        "dividend": 0.0, "split_coefficient": 1.0
    }
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from market_analysis.data.models._base import TimeseriesModel


class QuoteMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    source: str | None = None  # e.g. "alpha_vantage", "schwab"


class Quote(TimeseriesModel):
    metadata: QuoteMeta

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    adjusted_close: float | None = None
    volume: float | None = None
    dividend: float | None = None
    split_coefficient: float | None = None

    # Derived / optional (prototype carried this; keep for parity).
    candle: float | None = None
