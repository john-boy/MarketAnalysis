"""Account / position / sync-log models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from market_analysis.data.models._base import MongoModel, utcnow


AccessMode = Literal["api", "manual"]
TaxType = Literal[
    "taxable", "joint", "ira", "roth", "trust", "futures", "hsa", "529", "other"
]
SyncMode = Literal["api", "manual", "migration", "seed"]
SyncStatus = Literal["running", "success", "error"]


class Account(MongoModel):
    """A brokerage account.

    ``account_id`` holds the broker-assigned identifier for API accounts
    (populated after first sync) or a user-chosen slug for manual
    accounts.  ``nickname`` is the stable, human-readable unique key
    and carries the unique index.
    """

    account_id: str                       # broker-assigned or slug
    broker: str                           # "schwab", "fidelity", ...
    nickname: str                         # unique; UI label
    access_mode: AccessMode
    tax_type: TaxType
    credentials_ref: str | None = None    # lookup key in secrets, if any
    active: bool = True
    notes: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Position(MongoModel):
    account_id: str
    symbol: str
    qty: float
    avg_cost: float | None = None
    market_value: float | None = None
    as_of: datetime = Field(default_factory=utcnow)


class AccountSyncLog(MongoModel):
    account_id: str
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: datetime | None = None
    mode: SyncMode
    status: SyncStatus = "running"
    message: str | None = None
    diffs: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
