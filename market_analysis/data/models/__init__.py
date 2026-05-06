"""Pydantic models for every Mongo collection.

All models expose :meth:`to_mongo` returning a dict suitable for
``insert_*`` / ``update_*``.  Models default to ``extra="ignore"``
(regular collections) or ``extra="allow"`` (time-series) so they
tolerate prototype / AV payload drift without rejecting reads.

Mongo-side ``$jsonSchema`` validators are intentionally deferred — the
ingest layer enforces shape on the write path via these models; adding
Mongo-side validators couples us to a schema dialect translation that
isn't worth the effort until schemas stabilize.
"""

from market_analysis.data.models._base import (
    MongoModel,
    TimeseriesModel,
    utcnow,
)
from market_analysis.data.models.account import (
    AccessMode,
    Account,
    AccountSyncLog,
    Position,
    SyncMode,
    SyncStatus,
    TaxType,
)
from market_analysis.data.models.company import Company
from market_analysis.data.models.etf import ETF, Holding
from market_analysis.data.models.extractor import Extractor, ExtractorKind
from market_analysis.data.models.filing import Filing
from market_analysis.data.models.indicator import Indicator, IndicatorMeta
from market_analysis.data.models.market_index import IndexCategory, MarketIndex
from market_analysis.data.models.news import NewsItem
from market_analysis.data.models.quote import Quote, QuoteMeta
from market_analysis.data.models.theme import (
    DocType,
    Theme,
    ThemeDocument,
    ThemeGroup,
)
from market_analysis.data.models.watchlist import WatchlistEntry

__all__ = [
    "AccessMode",
    "Account",
    "AccountSyncLog",
    "Company",
    "DocType",
    "ETF",
    "Extractor",
    "ExtractorKind",
    "Filing",
    "Holding",
    "IndexCategory",
    "Indicator",
    "IndicatorMeta",
    "MarketIndex",
    "MongoModel",
    "NewsItem",
    "Position",
    "Quote",
    "QuoteMeta",
    "SyncMode",
    "SyncStatus",
    "TaxType",
    "Theme",
    "ThemeDocument",
    "ThemeGroup",
    "TimeseriesModel",
    "WatchlistEntry",
    "utcnow",
]
