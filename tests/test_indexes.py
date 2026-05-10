"""Tests for the MarketIndex model and the index ingestor's dispatch logic.

No Mongo / AV I/O — we drive the public functions with fakes so the
test is fast and isolated.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from market_analysis.data.models import MarketIndex


# -- Model ---------------------------------------------------------------


def test_proxy_mode_model():
    rec = MarketIndex(symbol="SPX", proxy_symbol="SPY")
    assert rec.mode == "proxy"
    assert rec.price_source_symbol == "SPY"


def test_direct_mode_model():
    rec = MarketIndex(symbol="VIX", fetch_symbol="VIX")
    assert rec.mode == "direct"
    assert rec.price_source_symbol == "VIX"


def test_both_modes_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        MarketIndex(symbol="BAD", proxy_symbol="X", fetch_symbol="Y")


def test_neither_mode_rejected():
    with pytest.raises(ValueError, match="exactly one"):
        MarketIndex(symbol="BAD")


def test_direct_mode_with_alias():
    """Canonical symbol may differ from provider's fetch ticker."""
    rec = MarketIndex(symbol="VIX", fetch_symbol="^VIX")
    assert rec.mode == "direct"
    assert rec.price_source_symbol == "VIX"  # stored under canonical


# -- Ingestor dispatch ---------------------------------------------------


@pytest.fixture
def fake_rec_proxy():
    return MarketIndex(symbol="SPX", proxy_symbol="SPY")


@pytest.fixture
def fake_rec_direct():
    return MarketIndex(symbol="VIX", fetch_symbol="VIX")


def test_ingest_index_unknown_symbol():
    from market_analysis.services.ingestors import indexes as ix
    with patch.object(ix, "_load_index", return_value=None):
        with pytest.raises(KeyError):
            ix.ingest_index("NOPE")


def test_ingest_index_proxy_ok(fake_rec_proxy):
    from market_analysis.services.ingestors import indexes as ix
    with patch.object(ix, "_load_index", return_value=fake_rec_proxy), \
         patch.object(ix, "_stamp") as stamp, \
         patch.object(ix, "mongo") as mongo_mod:
        mongo_mod.price_history.return_value.find_one.return_value = {
            "date": "2024-01-03",
        }
        r = ix.ingest_index("SPX")
    assert r.mode == "proxy"
    assert r.proxy_symbol == "SPY"
    assert r.error is None
    stamp.assert_called_once_with("SPX", ok=True)


def test_ingest_index_proxy_missing_data(fake_rec_proxy):
    from market_analysis.services.ingestors import indexes as ix
    with patch.object(ix, "_load_index", return_value=fake_rec_proxy), \
         patch.object(ix, "_stamp") as stamp, \
         patch.object(ix, "mongo") as mongo_mod:
        mongo_mod.price_history.return_value.find_one.return_value = None
        r = ix.ingest_index("SPX")
    assert r.error is not None
    assert "proxy" in r.error.lower()
    stamp.assert_called_once()
    assert stamp.call_args.kwargs["ok"] is False


def test_ingest_index_direct_canonical_equals_fetch(fake_rec_direct):
    from market_analysis.services.ingestors import indexes as ix
    from market_analysis.services.ingestors import prices as price_ingestor

    fake_report = price_ingestor.PriceIngestReport(
        symbol="VIX", mode="incremental", inserted=3,
    )
    with patch.object(ix, "_load_index", return_value=fake_rec_direct), \
         patch.object(ix, "_stamp") as stamp, \
         patch.object(ix, "_ingest_direct_with_alias",
                      return_value=fake_report) as alias:
        r = ix.ingest_index("VIX", client=MagicMock())
    assert r.mode == "direct"
    assert r.inserted == 3
    assert r.error is None
    # Direct mode always routes through the helper — even when the
    # canonical symbol matches the fetch ticker — because INDEX_DATA
    # is the correct endpoint for indexes (not TIME_SERIES_*).
    alias.assert_called_once()
    args = alias.call_args.args
    assert args[0] == "VIX" and args[1] == "VIX"
    stamp.assert_called_once_with("VIX", ok=True)


def test_ingest_index_direct_alias_routes_through_helper():
    from market_analysis.services.ingestors import indexes as ix
    from market_analysis.services.ingestors import prices as price_ingestor

    rec = MarketIndex(symbol="VIX", fetch_symbol="^VIX")
    fake_report = price_ingestor.PriceIngestReport(
        symbol="VIX", mode="full", inserted=500,
    )
    with patch.object(ix, "_load_index", return_value=rec), \
         patch.object(ix, "_stamp"), \
         patch.object(ix, "_ingest_direct_with_alias",
                      return_value=fake_report) as alias:
        ix.ingest_index("VIX", client=MagicMock())
    alias.assert_called_once()
    # Arg order: canonical, fetch_sym, client, mode
    args = alias.call_args.args
    assert args[0] == "VIX"
    assert args[1] == "^VIX"


def test_ingest_index_direct_av_error_recorded(fake_rec_direct):
    from market_analysis.services.ingestors import indexes as ix
    from market_analysis.sources.alpha_vantage import AlphaVantageError

    with patch.object(ix, "_load_index", return_value=fake_rec_direct), \
         patch.object(ix, "_stamp") as stamp, \
         patch.object(ix, "_ingest_direct_with_alias",
                      side_effect=AlphaVantageError("quota")):
        r = ix.ingest_index("VIX", client=MagicMock())
    assert r.error is not None
    assert "AV error" in r.error
    assert stamp.call_args.kwargs["ok"] is False
