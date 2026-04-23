"""ETF model — one doc per ETF in ``etf``."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from market_analysis.data.models._base import MongoModel, utcnow


class Holding(BaseModel):
    model_config = ConfigDict(extra="ignore")

    symbol: str
    name: str | None = None
    weight: float | None = None  # 0..1


class ETF(MongoModel):
    symbol: str
    name: str | None = None
    provider: str | None = None           # issuer, e.g. "SPDR"
    description: str | None = None
    expense_ratio: float | None = None    # 0..1 (AV reports as fraction)
    asset_class: str | None = None

    holdings: list[Holding] = Field(default_factory=list)
    sector_weights: dict[str, float] = Field(default_factory=dict)

    last_updated: datetime = Field(default_factory=utcnow)
