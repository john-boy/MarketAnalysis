"""Tests for the INDEX_DATA fetch path and the n/a holding filter.

Covers:
- ``AlphaVantageClient.index_data`` builds the right request.
- ``_parse_index_daily`` shapes an INDEX_DATA payload into
  ``daily_quotes`` docs (OHLC + candle, no volume/dividend requirement).
- ``is_tradeable_symbol`` rejects common non-tradeable holding
  sentinels and preserves real tickers.
- ``_parse_holdings`` drops non-tradeable rows.
- ``ingest_prices`` early-returns for non-tradeable symbols without
  calling AV.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from market_analysis.services.ingestors._common import is_tradeable_symbol
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import indexes as index_ingestor
from market_analysis.services.ingestors import prices as price_ingestor


# -- n/a filter ---------------------------------------------------------


def test_is_tradeable_symbol_accepts_real_tickers():
    for t in ("AAPL", "brk.b", "VIX", "BRK-A"):
        assert is_tradeable_symbol(t) is True


def test_is_tradeable_symbol_rejects_sentinels():
    for t in ("n/a", "N/A", "cash", "USD", "OTHER", "-", "", None, " "):
        assert is_tradeable_symbol(t) is False


def test_parse_holdings_skips_non_tradeable():
    raw = [
        {"symbol": "AAPL", "description": "Apple"},
        {"symbol": "n/a",  "description": "Cash bucket"},
        {"symbol": "MSFT", "description": "Microsoft"},
        {"symbol": "",     "description": "empty"},
    ]
    holdings = etf_ingestor._parse_holdings(raw)
    syms = [h.symbol for h in holdings]
    assert syms == ["AAPL", "MSFT"]


def test_ingest_prices_short_circuits_for_non_tradeable():
    av = MagicMock()
    r = price_ingestor.ingest_prices("n/a", client=av, mode="auto")
    assert r.inserted == 0
    assert r.first_date is None
    # Crucially, AV must NOT have been called.
    av.time_series_daily_adjusted.assert_not_called()


# -- INDEX_DATA endpoint -----------------------------------------------


def test_alpha_vantage_index_data_builds_request():
    from market_analysis.sources.alpha_vantage import AlphaVantageClient

    client = AlphaVantageClient(api_key="testkey")
    with patch.object(client, "_get", return_value={"Time Series (Daily)": {}}) as g:
        client.index_data("VIX", interval="daily")
    params = g.call_args.args[0]
    assert params["function"] == "INDEX_DATA"
    assert params["symbol"] == "VIX"
    assert params["interval"] == "daily"


def test_parse_index_daily_live_shape():
    """AV INDEX_DATA returns a list under ``data`` with bare field names."""
    payload = {
        "symbol": "VIX",
        "name": "CBOE Volatility Index",
        "interval": "daily",
        "data": [
            # Newest first (as AV returns it).
            {"date": "2024-01-03", "open": "14.04", "high": "14.81",
             "low": "13.88", "close": "14.24"},
            {"date": "2024-01-02", "open": "12.45", "high": "13.70",
             "low": "12.30", "close": "13.20"},
        ],
    }
    docs = price_ingestor._parse_index_daily(payload, "VIX")
    assert len(docs) == 2
    # Sorted ascending by date regardless of input ordering.
    assert docs[0]["date"] < docs[1]["date"]
    d = docs[1]
    assert d["metadata"] == {
        "symbol": "VIX", "source": "alpha_vantage", "asset_type": "index",
    }
    assert d["open"] == 14.04
    assert d["high"] == 14.81
    assert d["low"] == 13.88
    assert d["close"] == 14.24
    from pytest import approx
    assert d["candle"] == approx(d["close"] - d["open"])
    assert "volume" not in d
    assert "adjusted_close" not in d


def test_parse_index_daily_legacy_dict_shape():
    """Older cassettes used a Time-Series-dict shape; still parseable."""
    payload = {
        "Time Series (Daily)": {
            "2024-01-03": {"1. open": "4700.12", "2. high": "4750.50",
                           "3. low": "4690.00", "4. close": "4725.75"},
        },
    }
    docs = price_ingestor._parse_index_daily(payload, "SPX")
    assert len(docs) == 1
    assert docs[0]["close"] == 4725.75


def test_parse_index_daily_empty_payload():
    assert price_ingestor._parse_index_daily({}, "SPX") == []
    assert price_ingestor._parse_index_daily({"data": []}, "SPX") == []


# -- Direct-mode index ingest uses INDEX_DATA --------------------------


def test_direct_mode_ingest_calls_index_data():
    """Even when canonical == fetch_symbol, we route through INDEX_DATA.

    The regression target: previously, ``fetch_sym == sym`` would fall
    back to ``TIME_SERIES_DAILY_ADJUSTED``, which returns errors for
    real indexes.  Now the direct-mode helper always uses INDEX_DATA.
    """
    from market_analysis.data.models import MarketIndex

    rec = MarketIndex(symbol="VIX", fetch_symbol="VIX")
    av = MagicMock()
    av.index_data.return_value = {"data": []}
    with patch.object(index_ingestor, "_load_index", return_value=rec), \
         patch.object(index_ingestor, "_stamp"), \
         patch.object(index_ingestor, "mongo") as mongo_mod:
        mongo_mod.price_history.return_value.find_one.return_value = None
        mongo_mod.price_history.return_value.delete_many.return_value.deleted_count = 0
        index_ingestor.ingest_index("VIX", client=av, mode="auto")
    av.index_data.assert_called_once()
    av.time_series_daily_adjusted.assert_not_called()
