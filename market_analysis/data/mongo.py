"""MongoDB connection and typed collection accessors.

Single place that knows the database name and collection names.
Callers obtain collections via the functions below — never via string
literals scattered across the codebase.

Usage::

    from market_analysis.data import mongo

    for quote in mongo.daily_quotes().find({"symbol": "SPY"}):
        ...

All accessors read the configured DB name lazily, so
``market_analysis.services.config`` changes (via
``reload_settings``) are reflected on the next call.
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from market_analysis.services.config import get_settings


# -- Collection name constants --------------------------------------------
# Keep these lowercase_snake_case and mirror ``docs/plan.md`` § 5.2.

COMPANIES = "companies"
ETF = "etf"
INDEXES = "indexes"
DAILY_QUOTES = "daily_quotes"
INDICATORS = "indicators"
THEME_GROUPS = "theme_groups"
THEMES = "themes"
THEME_DOCUMENTS = "theme_documents"
WATCHLIST = "watchlist"
ACCOUNTS = "accounts"
POSITIONS = "positions"
ACCOUNT_SYNC_LOG = "account_sync_log"
FILINGS = "filings"
NEWS = "news"
PIPELINE_DEFINITIONS = "pipeline_definitions"
SCHEMA_VERSION = "schema_version"

#: Time-series collections, mapped to their creation options.
#: Both are daily data; the prototype was bucket-per-day and we mirror that.
#: ``metaField='metadata'`` tracks the real document shape (the prototype
#: collections declared ``'symbol'`` / ``'indicator'`` but wrote
#: ``{date, metadata: {...}, ...}``, leaving the declaration unused).
#: See ADR-0008.
TIMESERIES_OPTIONS: dict[str, dict] = {
    "daily_quotes": {
        "timeField": "date",
        "metaField": "metadata",
        # 1-day buckets, mirroring the prototype exactly.
        "bucketRoundingSeconds": 86400,
        "bucketMaxSpanSeconds": 86400,
    },
    "indicators": {
        "timeField": "date",
        "metaField": "metadata",
        "bucketRoundingSeconds": 86400,
        "bucketMaxSpanSeconds": 86400,
    },
}

#: Every collection the migration creates.  Order is informational.
ALL_COLLECTIONS: tuple[str, ...] = (
    COMPANIES,
    ETF,
    INDEXES,
    DAILY_QUOTES,
    INDICATORS,
    THEME_GROUPS,
    THEMES,
    THEME_DOCUMENTS,
    WATCHLIST,
    ACCOUNTS,
    POSITIONS,
    ACCOUNT_SYNC_LOG,
    FILINGS,
    NEWS,
    PIPELINE_DEFINITIONS,
    SCHEMA_VERSION,
)


# -- Connection -----------------------------------------------------------


@lru_cache(maxsize=1)
def client() -> MongoClient:
    """Return the process-wide ``MongoClient`` (cached)."""
    s = get_settings()
    return MongoClient(s.mongo.uri, serverSelectionTimeoutMS=2000)


def reset_client() -> None:
    """Clear the cached client — call after settings reload."""
    client.cache_clear()


def db(name: str | None = None) -> Database:
    """Return a ``Database`` handle (target DB by default)."""
    s = get_settings()
    return client()[name or s.mongo.database]


def prototype_db() -> Database:
    """Return the prototype DB (for migration reads)."""
    s = get_settings()
    return client()[s.mongo.prototype_database]


def ping(timeout_ms: int = 1000) -> bool:
    """Return True iff the Mongo server responds to ``ping`` in time."""
    s = get_settings()
    try:
        MongoClient(s.mongo.uri, serverSelectionTimeoutMS=timeout_ms) \
            .admin.command("ping")
        return True
    except Exception:
        return False


# -- Typed collection accessors -------------------------------------------


def companies() -> Collection:
    return db()[COMPANIES]


def etf() -> Collection:
    return db()[ETF]


def indexes() -> Collection:
    return db()[INDEXES]


def daily_quotes() -> Collection:
    return db()[DAILY_QUOTES]


def indicators() -> Collection:
    return db()[INDICATORS]


def theme_groups() -> Collection:
    return db()[THEME_GROUPS]


def themes() -> Collection:
    return db()[THEMES]


def theme_documents() -> Collection:
    return db()[THEME_DOCUMENTS]


def watchlist() -> Collection:
    return db()[WATCHLIST]


def accounts() -> Collection:
    return db()[ACCOUNTS]


def positions() -> Collection:
    return db()[POSITIONS]


def account_sync_log() -> Collection:
    return db()[ACCOUNT_SYNC_LOG]


def filings() -> Collection:
    return db()[FILINGS]


def news() -> Collection:
    return db()[NEWS]


def pipeline_definitions() -> Collection:
    return db()[PIPELINE_DEFINITIONS]


def schema_version() -> Collection:
    return db()[SCHEMA_VERSION]
