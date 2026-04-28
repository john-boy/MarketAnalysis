"""Tests for the Mongo accessor module.

These tests verify the static structure only (collection-name
constants, ``ALL_COLLECTIONS`` completeness).  Tests that require a
live MongoDB server belong in an integration suite gated by
``MARKET_ANALYSIS_INTEGRATION``.
"""

from __future__ import annotations

from market_analysis.data import mongo


def test_all_collections_are_unique():
    assert len(mongo.ALL_COLLECTIONS) == len(set(mongo.ALL_COLLECTIONS))


def test_all_collections_contains_each_name_constant():
    expected = {
        mongo.COMPANIES, mongo.ETF, mongo.INDEXES,
        mongo.DAILY_QUOTES, mongo.INDICATORS,
        mongo.THEME_GROUPS, mongo.THEMES, mongo.THEME_DOCUMENTS,
        mongo.WATCHLIST, mongo.ACCOUNTS, mongo.POSITIONS,
        mongo.ACCOUNT_SYNC_LOG, mongo.FILINGS, mongo.NEWS,
        mongo.PIPELINE_DEFINITIONS, mongo.SCHEMA_VERSION,
    }
    assert expected == set(mongo.ALL_COLLECTIONS)


def test_collection_names_are_snake_case():
    for name in mongo.ALL_COLLECTIONS:
        assert name == name.lower()
        assert " " not in name
        assert "-" not in name
