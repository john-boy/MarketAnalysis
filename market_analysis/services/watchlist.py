"""Watchlist management.

CRUD for the ``watchlist`` collection: adding and removing tickers,
tagging with themes and ingest tags, querying opt-ins that drive
the poller's fan-out.

Mutations live here; read helpers (``list_watchlist``) live in
:mod:`market_analysis.services.queries` alongside the rest of the
UI-facing reads.
"""

from __future__ import annotations

from market_analysis.data import mongo
from market_analysis.data.models import WatchlistEntry, utcnow


def add_entry(
    symbol: str,
    *,
    themes: list[str] | None = None,
    ingest_tags: list[str] | None = None,
    notes: str | None = None,
    source_of_origin: str | None = "manual",
) -> bool:
    """Insert a new watchlist entry.  Returns True iff it was created.

    Existing rows are left untouched — use :func:`update_entry` to
    modify tags or notes in place.
    """
    sym = symbol.upper()
    entry = WatchlistEntry(
        symbol=sym,
        themes=themes or [],
        ingest_tags=ingest_tags or [],
        notes=notes,
        source_of_origin=source_of_origin,
    )
    res = mongo.watchlist().update_one(
        {"symbol": sym},
        {"$setOnInsert": entry.to_mongo()},
        upsert=True,
    )
    return res.upserted_id is not None


def remove_entry(symbol: str) -> bool:
    """Remove a watchlist entry.  Returns True iff a row was deleted."""
    res = mongo.watchlist().delete_one({"symbol": symbol.upper()})
    return res.deleted_count > 0


def update_entry(
    symbol: str,
    *,
    themes: list[str] | None = None,
    ingest_tags: list[str] | None = None,
    notes: str | None = None,
) -> bool:
    """Update mutable fields on a watchlist entry in place.

    ``None`` means "leave unchanged".  Always bumps ``last_updated``.
    Returns True iff a row was matched.
    """
    sym = symbol.upper()
    sets: dict = {"last_updated": utcnow()}
    if themes is not None:
        sets["themes"] = themes
    if ingest_tags is not None:
        sets["ingest_tags"] = ingest_tags
    if notes is not None:
        sets["notes"] = notes
    res = mongo.watchlist().update_one({"symbol": sym}, {"$set": sets})
    return res.matched_count > 0
