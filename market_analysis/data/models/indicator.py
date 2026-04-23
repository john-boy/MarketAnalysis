"""Indicator model — time-series ``indicators``.

Prototype-compatible shape::

    {
        "date": <datetime>,
        "metadata": {"symbol": "AAPL", "indicator": "ema", "stack": 5},
        "short": 170.5, "middle": 170.5, "long": 170.5
    }

Indicator payloads vary by type (EMA has short/middle/long; RSI has
``value``; MACD has ``macd``/``signal``/``hist``).  We therefore allow
arbitrary extra top-level fields on the parent :class:`TimeseriesModel`
and keep only the well-known metadata fields typed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from market_analysis.data.models._base import TimeseriesModel


class IndicatorMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    indicator: str            # "ema", "rsi", "macd", ...
    stack: int | None = None  # prototype convention for indicator variants
    params: dict[str, Any] = Field(default_factory=dict)


class Indicator(TimeseriesModel):
    metadata: IndicatorMeta
    # Values (``short``, ``middle``, ``long``, ``value``, ``signal``,
    # ``hist``, …) arrive as extras and are preserved by ``extra="allow"``
    # on the base class.
