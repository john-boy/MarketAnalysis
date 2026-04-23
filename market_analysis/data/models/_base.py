"""Base classes for MongoDB-backed Pydantic models.

Two flavors:

- :class:`MongoModel` — documents in regular collections.  Extra fields
  from Mongo are silently ignored on load.
- :class:`TimeseriesModel` — documents in time-series collections.
  Extra top-level fields are preserved (``extra="allow"``) because
  time-series payloads are open-ended (e.g. indicator docs have
  ``short``/``middle``/``long`` keys that vary by indicator type).

Both expose :meth:`to_mongo` which returns a dict suitable for
``insert_*`` / ``update_*``, stripping ``_id`` when it is ``None``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Naïve-free UTC timestamp — use everywhere we write times."""
    return datetime.now(timezone.utc)


class MongoModel(BaseModel):
    """Base for regular-collection documents."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="ignore",
    )

    id: Any | None = Field(default=None, alias="_id")

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=False)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data


class TimeseriesModel(BaseModel):
    """Base for time-series-collection documents."""

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="allow",
    )

    id: Any | None = Field(default=None, alias="_id")
    date: datetime

    def to_mongo(self) -> dict[str, Any]:
        data = self.model_dump(by_alias=True, exclude_none=False)
        if data.get("_id") is None:
            data.pop("_id", None)
        return data
