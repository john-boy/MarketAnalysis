"""Tests for the Mongo accessor module.

These tests verify the static structure only (collection-name
constants, ``WYCKOFF_COLLECTIONS`` completeness).  Tests that require
a live MongoDB server belong in an integration suite gated by
``MARKET_ANALYSIS_INTEGRATION``.
"""

from __future__ import annotations

from market_analysis.data import mongo


def test_wyckoff_collections_are_unique():
    assert len(mongo.WYCKOFF_COLLECTIONS) == len(set(mongo.WYCKOFF_COLLECTIONS))


def test_wyckoff_collections_contains_each_wyckoff_constant():
    expected = {
        mongo.PRICE_HISTORY, mongo.INDICATORS, mongo.COMPANIES, mongo.ETF,
        mongo.INDEXES, mongo.WATCHLIST, mongo.SPLITS_EVENTS, mongo.FEATURES,
        mongo.PHASE_LABELS, mongo.TRANSITIONS, mongo.PROJECTIONS,
    }
    assert expected == set(mongo.WYCKOFF_COLLECTIONS)


def test_collection_names_are_snake_case():
    for name in mongo.WYCKOFF_COLLECTIONS:
        assert name == name.lower()
        assert " " not in name
        assert "-" not in name
