"""SEC filing model (federated from Edgar_Monitor)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


class Filing(MongoModel):
    accession_number: str                 # unique; carries the unique index
    cik: str
    symbol: str | None = None
    form_type: str                        # "8-K", "S-1", "S-1/A", "424B4", ...
    filing_date: datetime
    url: str | None = None
    summary: str | None = None
    tags: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=utcnow)
