"""Tests for the Pydantic data models.

These tests exercise:

- construction with both field names and ``_id`` alias,
- ``to_mongo()`` strips ``_id`` when it's ``None``,
- time-series models preserve extras (``extra="allow"``),
- regular models drop unknown fields (``extra="ignore"``),
- Literal enums (account / sync types) reject bad values.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from market_analysis.data.models import (
    Account,
    AccountSyncLog,
    Company,
    Filing,
    Indicator,
    IndicatorMeta,
    NewsItem,
    Position,
    Quote,
    QuoteMeta,
    Theme,
    ThemeDocument,
    ThemeGroup,
    WatchlistEntry,
    utcnow,
)


# -- Base behavior --------------------------------------------------------


def test_to_mongo_strips_null_id():
    c = Company(symbol="AAPL")
    doc = c.to_mongo()
    assert "_id" not in doc
    assert doc["symbol"] == "AAPL"


def test_alias_id_roundtrip():
    c = Company(symbol="AAPL", _id="abc123")
    doc = c.to_mongo()
    assert doc["_id"] == "abc123"


def test_regular_model_ignores_extras():
    # MongoModel has extra="ignore"
    c = Company(symbol="AAPL", bogus_field="x")  # type: ignore[call-arg]
    assert not hasattr(c, "bogus_field")


# -- Time-series models ---------------------------------------------------


def test_quote_construct_and_to_mongo():
    q = Quote(
        date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        metadata=QuoteMeta(symbol="AAPL", source="alpha_vantage"),
        open=100.0, high=101.0, low=99.0, close=100.5,
        volume=1_000_000,
    )
    doc = q.to_mongo()
    assert "_id" not in doc
    assert doc["metadata"]["symbol"] == "AAPL"
    assert doc["close"] == 100.5
    assert isinstance(doc["date"], datetime)


def test_indicator_preserves_extras():
    # TimeseriesModel has extra="allow" — short/middle/long pass through.
    ind = Indicator(
        date=datetime(2024, 1, 2, tzinfo=timezone.utc),
        metadata=IndicatorMeta(symbol="AAPL", indicator="ema", stack=5),
        short=170.1, middle=170.2, long=170.3,  # type: ignore[call-arg]
    )
    doc = ind.to_mongo()
    assert doc["short"] == 170.1
    assert doc["middle"] == 170.2
    assert doc["long"] == 170.3
    assert doc["metadata"]["indicator"] == "ema"


# -- Account models -------------------------------------------------------


def test_account_literal_validation():
    Account(
        account_id="schwab-ira",
        broker="schwab",
        nickname="Schwab IRA",
        access_mode="api",
        tax_type="ira",
    )
    with pytest.raises(ValidationError):
        Account(
            account_id="x",
            broker="schwab",
            nickname="Bad",
            access_mode="api",
            tax_type="crypto",  # not in TaxType literal
        )
    with pytest.raises(ValidationError):
        Account(
            account_id="x",
            broker="schwab",
            nickname="Bad",
            access_mode="scraper",  # not in AccessMode literal
            tax_type="ira",
        )


def test_sync_log_defaults():
    log = AccountSyncLog(account_id="schwab-ira", mode="migration")
    assert log.status == "running"
    assert log.started_at.tzinfo is not None
    assert log.diffs == {}
    assert log.errors == []


def test_position_basic():
    p = Position(account_id="schwab-ira", symbol="AAPL", qty=10.0, avg_cost=150.0)
    doc = p.to_mongo()
    assert doc["qty"] == 10.0
    assert doc["symbol"] == "AAPL"


# -- Content models -------------------------------------------------------


def test_theme_document_type_literal():
    ThemeDocument(path="/a.md", type="analysis")
    with pytest.raises(ValidationError):
        ThemeDocument(path="/a.md", type="garbage")  # type: ignore[arg-type]


def test_theme_group_and_theme_defaults():
    g = ThemeGroup(name="AI")
    assert g.created_at.tzinfo is not None
    t = Theme(name="AI Infrastructure")
    assert t.active is True
    assert t.groups == []
    assert t.tickers == []


def test_watchlist_defaults():
    w = WatchlistEntry(symbol="AAPL")
    assert w.themes == []
    assert w.ingest_tags == []


def test_filing_fields():
    f = Filing(
        accession_number="0001234567-24-000001",
        cik="0000320193",
        form_type="8-K",
        filing_date=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    doc = f.to_mongo()
    assert doc["accession_number"].startswith("0001234567")
    assert doc["tags"] == []


def test_news_item_fields():
    n = NewsItem(
        hash="abc",
        title="T",
        url="https://example.com",
        published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    assert n.symbols == []
    assert n.sentiment_score is None


# -- utcnow ---------------------------------------------------------------


def test_utcnow_is_aware_utc():
    now = utcnow()
    assert now.tzinfo is timezone.utc
