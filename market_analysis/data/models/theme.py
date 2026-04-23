"""Theme group, theme, and theme-document models."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


DocType = Literal["analysis", "news-clip", "rationale", "post-mortem", "other"]


class ThemeGroup(MongoModel):
    name: str
    description: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Theme(MongoModel):
    name: str
    description: str | None = None
    groups: list[str] = Field(default_factory=list)
    home_document_path: str | None = None
    #: Union of tickers across theme's documents — maintained by the indexer.
    tickers: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ThemeDocument(MongoModel):
    path: str                               # absolute or iCloud-relative
    title: str | None = None
    themes: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    type: DocType | None = None
    mtime: datetime | None = None
    hash: str | None = None                 # content hash for change detection
    is_home: bool = False
