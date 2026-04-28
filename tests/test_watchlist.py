"""Tests for the watchlist CRUD service and the enriched list query.

Mongo is faked — we verify the correct filters and update operators
reach the driver, not actual DB behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from market_analysis.services import queries
from market_analysis.services import watchlist as wl


# -- add_entry -----------------------------------------------------------


def test_add_entry_inserts_new():
    fake = MagicMock()
    fake.update_one.return_value = MagicMock(upserted_id="abc")
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        inserted = wl.add_entry(
            "aapl",
            themes=["ai"],
            ingest_tags=["news"],
            notes="watch earnings",
        )
    assert inserted is True
    filt, update = fake.update_one.call_args.args
    assert filt == {"symbol": "AAPL"}
    assert "$setOnInsert" in update
    doc = update["$setOnInsert"]
    assert doc["symbol"] == "AAPL"
    assert doc["themes"] == ["ai"]
    assert doc["ingest_tags"] == ["news"]
    assert fake.update_one.call_args.kwargs["upsert"] is True


def test_add_entry_existing_returns_false():
    fake = MagicMock()
    fake.update_one.return_value = MagicMock(upserted_id=None)
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        assert wl.add_entry("AAPL") is False


# -- remove_entry --------------------------------------------------------


def test_remove_entry_deletes_by_symbol():
    fake = MagicMock()
    fake.delete_one.return_value = MagicMock(deleted_count=1)
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        assert wl.remove_entry("aapl") is True
    fake.delete_one.assert_called_once_with({"symbol": "AAPL"})


def test_remove_entry_missing_returns_false():
    fake = MagicMock()
    fake.delete_one.return_value = MagicMock(deleted_count=0)
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        assert wl.remove_entry("MISS") is False


# -- update_entry --------------------------------------------------------


def test_update_entry_sets_only_provided_fields():
    fake = MagicMock()
    fake.update_one.return_value = MagicMock(matched_count=1)
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        assert wl.update_entry("aapl", themes=["ai"], notes="n") is True
    filt, update = fake.update_one.call_args.args
    assert filt == {"symbol": "AAPL"}
    sets = update["$set"]
    assert sets["themes"] == ["ai"]
    assert sets["notes"] == "n"
    assert "ingest_tags" not in sets  # None => not set
    assert "last_updated" in sets


def test_update_entry_missing_returns_false():
    fake = MagicMock()
    fake.update_one.return_value = MagicMock(matched_count=0)
    with patch("market_analysis.services.watchlist.mongo.watchlist", return_value=fake):
        assert wl.update_entry("MISS", themes=[]) is False


# -- list_watchlist (read helper) ----------------------------------------


def test_list_watchlist_enriches_with_last_quote_date():
    wl_coll = MagicMock()
    dq_coll = MagicMock()

    # Two watchlist rows.
    wl_docs = [
        {
            "symbol": "AAPL", "themes": ["ai"], "ingest_tags": ["news"],
            "source_of_origin": "manual", "notes": None,
            "added_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "last_updated": None,
        },
        {
            "symbol": "MSFT", "themes": [], "ingest_tags": [],
            "source_of_origin": "manual", "notes": None,
            "added_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "last_updated": None,
        },
    ]
    wl_cursor = MagicMock()
    wl_cursor.sort.return_value = iter(wl_docs)
    wl_coll.find.return_value = wl_cursor

    # AAPL has a quote, MSFT doesn't.
    def _find_one(filt, **kwargs):
        if filt["metadata.symbol"] == "AAPL":
            return {"date": datetime(2026, 3, 1, tzinfo=timezone.utc)}
        return None
    dq_coll.find_one.side_effect = _find_one

    with patch("market_analysis.services.queries.mongo.watchlist", return_value=wl_coll), \
         patch("market_analysis.services.queries.mongo.daily_quotes", return_value=dq_coll):
        rows = queries.list_watchlist()

    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["last_quote_date"] is not None
    assert rows[1]["symbol"] == "MSFT"
    assert rows[1]["last_quote_date"] is None
