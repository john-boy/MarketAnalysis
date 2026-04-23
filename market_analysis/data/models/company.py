"""Company model — one doc per equity symbol in ``companies``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


class Company(MongoModel):
    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    exchange: str | None = None
    country: str | None = None
    currency: str | None = None
    cik: str | None = None
    fiscal_year_end: str | None = None
    description: str | None = None

    #: Full AV ``OVERVIEW``-style payload.  Open-ended because AV
    #: occasionally adds fields; consumers read what they need.
    fundamentals: dict[str, Any] = Field(default_factory=dict)

    #: ETFs known to hold this symbol (population maintained by the ETF
    #: ingestor).
    etf_memberships: list[str] = Field(default_factory=list)

    last_updated: datetime = Field(default_factory=utcnow)
