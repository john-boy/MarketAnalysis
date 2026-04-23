"""Tests for ingestor payload normalization.

Mongo writes are not covered here — the CLI path exercises them
end-to-end.  These tests verify we translate Alpha Vantage payloads
into the expected Mongo document shapes.
"""

from __future__ import annotations

from market_analysis.services.ingestors.etf import (
    _etf_from_payload,
    _parse_holdings,
    _parse_sector_weights,
)
from market_analysis.services.ingestors.prices import _parse_daily_adjusted


# -- Prices --------------------------------------------------------------


def test_parse_daily_adjusted_basic():
    payload = {
        "Meta Data": {"2. Symbol": "AAPL"},
        "Time Series (Daily)": {
            "2024-01-03": {
                "1. open": "184.22", "2. high": "185.88", "3. low": "183.43",
                "4. close": "184.25", "5. adjusted close": "184.25",
                "6. volume": "58414500", "7. dividend amount": "0.0000",
                "8. split coefficient": "1.0",
            },
            "2024-01-02": {
                "1. open": "187.15", "2. high": "188.44", "3. low": "183.89",
                "4. close": "185.64", "5. adjusted close": "185.64",
                "6. volume": "82488700", "7. dividend amount": "0.0000",
                "8. split coefficient": "1.0",
            },
        },
    }
    docs = _parse_daily_adjusted(payload, "AAPL")
    assert len(docs) == 2
    # Sorted ascending by date.
    assert docs[0]["date"] < docs[1]["date"]
    d = docs[1]
    assert d["metadata"] == {"symbol": "AAPL", "source": "alpha_vantage"}
    assert d["open"] == 184.22
    assert d["close"] == 184.25
    assert d["adjusted_close"] == 184.25
    assert d["volume"] == 58414500.0
    assert d["dividend"] == 0.0
    assert d["split_coefficient"] == 1.0
    assert d["candle"] == d["close"] - d["open"]


def test_parse_daily_adjusted_empty_series():
    assert _parse_daily_adjusted({}, "AAPL") == []
    assert _parse_daily_adjusted({"Time Series (Daily)": {}}, "AAPL") == []


def test_parse_daily_adjusted_skips_bad_row_fields():
    payload = {
        "Time Series (Daily)": {
            "2024-01-02": {
                "1. open": "not-a-number",
                "4. close": "100.0",
            },
        },
    }
    docs = _parse_daily_adjusted(payload, "AAPL")
    assert len(docs) == 1
    assert "open" not in docs[0]  # bad cast skipped
    assert docs[0]["close"] == 100.0


# -- ETF -----------------------------------------------------------------


def test_parse_holdings_filters_missing_symbol():
    out = _parse_holdings([
        {"symbol": "AAPL", "description": "Apple Inc", "weight": "0.07"},
        {"description": "Missing symbol", "weight": "0.01"},
        {"symbol": "MSFT", "description": "Microsoft", "weight": None},
    ])
    syms = [h.symbol for h in out]
    assert syms == ["AAPL", "MSFT"]
    assert out[0].weight == 0.07
    assert out[1].weight is None


def test_parse_sector_weights_roundtrip():
    out = _parse_sector_weights([
        {"sector": "Technology", "weight": "0.32"},
        {"sector": "Healthcare", "weight": None},
        {"sector": "Financials", "weight": "0.13"},
    ])
    assert out == {"Technology": 0.32, "Financials": 0.13}


def test_etf_from_payload():
    payload = {
        "name": "SPDR S&P 500 ETF Trust",
        "issuer": "State Street",
        "asset_class": "EQUITY",
        "net_expense_ratio": "0.0009",
        "description": "Tracks the S&P 500.",
        "holdings": [
            {"symbol": "AAPL", "description": "Apple Inc", "weight": "0.07"},
            {"symbol": "MSFT", "description": "Microsoft", "weight": "0.065"},
        ],
        "sectors": [{"sector": "Technology", "weight": "0.32"}],
    }
    etf = _etf_from_payload("SPY", payload)
    assert etf.symbol == "SPY"
    assert etf.name.startswith("SPDR")
    assert etf.provider == "State Street"
    assert etf.expense_ratio == 0.0009
    assert len(etf.holdings) == 2
    assert etf.sector_weights == {"Technology": 0.32}
