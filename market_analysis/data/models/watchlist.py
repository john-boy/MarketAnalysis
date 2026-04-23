"""Watchlist entry model."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


class WatchlistEntry(MongoModel):
    symbol: str
    themes: list[str] = Field(default_factory=list)
    ingest_tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    #: How this entry arrived: "manual", "edgar-s1", "etf-holding", ...
    source_of_origin: str | None = None
    added_at: datetime = Field(default_factory=utcnow)
    last_updated: datetime | None = None
