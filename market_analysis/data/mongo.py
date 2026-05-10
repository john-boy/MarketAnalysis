"""MongoDB connection and typed collection accessors.

Single place that knows the database name and collection names.
Callers obtain collections via the functions below — never via string
literals scattered across the codebase.

Usage::

    from market_analysis.data import mongo

    for quote in mongo.price_history().find({"symbol": "SPY"}):
        ...

All accessors read the configured DB name lazily, so
``market_analysis.config`` changes (via
``reload_settings``) are reflected on the next call.
"""

from __future__ import annotations

from functools import lru_cache

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from market_analysis.config import get_settings


# -- Collection name constants --------------------------------------------
# Keep these lowercase_snake_case and mirror ``docs/plan.md`` § 5.2.

COMPANIES = "companies"
ETF = "etf"
INDEXES = "indexes"
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
EXTRACTORS = "extractors"
SCHEMA_VERSION = "schema_version"

# -- WyckoffDB additions (see docs/WYCKOFF_CODE_SPEC.md) ------------------

WYCKOFF_DB_NAME = "WyckoffDB"

PRICE_HISTORY = "price_history"      # TSC; replaces daily_quotes
SPLITS_EVENTS = "splits_events"
FEATURES = "features"
PHASE_LABELS = "phase_labels"
TRANSITIONS = "transitions"
PROJECTIONS = "projections"

#: Time-series collections, mapped to their creation options.
#: All are daily data; the prototype was bucket-per-day and we mirror that.
#: ``metaField='metadata'`` tracks the real document shape (the prototype
#: collections declared ``'symbol'`` / ``'indicator'`` but wrote
#: ``{date, metadata: {...}, ...}``, leaving the declaration unused).
#: See ADR-0008.
TIMESERIES_OPTIONS: dict[str, dict] = {
    PRICE_HISTORY: {
        "timeField": "date",
        "metaField": "metadata",
        # 1-day buckets, mirroring the prototype exactly.
        "bucketRoundingSeconds": 86400,
        "bucketMaxSpanSeconds": 86400,
    },
    INDICATORS: {
        "timeField": "date",
        "metaField": "metadata",
        "bucketRoundingSeconds": 86400,
        "bucketMaxSpanSeconds": 86400,
    },
}

#: Every WyckoffDB collection. Used by the setup script.
#: ``indexes`` and ``indicators`` are pragmatic carryovers: spec said not
#: to migrate them, but index ingestion would break and we want the
#: indicators TSC ready for fresh adj_close-based recompute.
WYCKOFF_COLLECTIONS: tuple[str, ...] = (
    PRICE_HISTORY,
    INDICATORS,
    COMPANIES,
    ETF,
    INDEXES,
    WATCHLIST,
    SPLITS_EVENTS,
    FEATURES,
    PHASE_LABELS,
    TRANSITIONS,
    PROJECTIONS,
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


def extractors() -> Collection:
    return db()[EXTRACTORS]


def schema_version() -> Collection:
    return db()[SCHEMA_VERSION]


# -- WyckoffDB additions (price_history, splits, features, etc.) ---------


def price_history() -> Collection:
    return db()[PRICE_HISTORY]


def splits_events() -> Collection:
    return db()[SPLITS_EVENTS]


def features() -> Collection:
    return db()[FEATURES]


def phase_labels() -> Collection:
    return db()[PHASE_LABELS]


def transitions() -> Collection:
    return db()[TRANSITIONS]


def projections() -> Collection:
    return db()[PROJECTIONS]
