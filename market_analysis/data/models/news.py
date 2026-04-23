"""News item model (Alpha Vantage News & Sentiment payloads)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


class NewsItem(MongoModel):
    #: Stable identifier used for dedup; typically a URL hash.
    hash: str

    title: str
    url: str
    published_at: datetime
    source: str | None = None              # domain / wire
    summary: str | None = None

    symbols: list[str] = Field(default_factory=list)
    sentiment_score: float | None = None   # -1..+1
    relevance_score: float | None = None   # 0..1

    fetched_at: datetime = Field(default_factory=utcnow)
