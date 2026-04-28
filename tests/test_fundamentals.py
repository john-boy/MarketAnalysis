"""Tests for the fundamentals ingestor and the fundamentals view.

Covers:
- Parsing AV OVERVIEW payloads into (top_level, fundamentals) dicts.
- Skip paths for empty / throttled / unsupported responses.
- End-to-end dispatch through ``ingest_fundamentals`` with a fake AV
  client and a fake Mongo collection.
- ``load_fundamentals_view`` rendering into labeled sections.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

from market_analysis.services import queries
from market_analysis.services.ingestors import fundamentals as fx
from market_analysis.sources.alpha_vantage import AlphaVantageError


# -- Parsing -------------------------------------------------------------


SAMPLE = {
    "Symbol": "AAPL",
    "Name": "Apple Inc",
    "Description": "Designs and sells smartphones.",
    "Exchange": "NASDAQ",
    "Currency": "USD",
    "Country": "USA",
    "Sector": "TECHNOLOGY",
    "Industry": "ELECTRONIC COMPUTERS",
    "CIK": "320193",
    "FiscalYearEnd": "September",
    "LatestQuarter": "2024-12-31",
    "MarketCapitalization": "3000000000000",
    "PERatio": "32.5",
    "ForwardPE": "29.8",
    "EPS": "6.01",
    "DividendYield": "0.0054",
    "ProfitMargin": "0.2530",
    "52WeekHigh": "199.62",
    "52WeekLow": "164.08",
    "SharesOutstanding": "15000000000",
    "AnalystTargetPrice": "220.00",
    "AnalystRatingStrongBuy": "11",
    "AnalystRatingBuy": "20",
    "AnalystRatingHold": "12",
    "AnalystRatingSell": "1",
    "AnalystRatingStrongSell": "0",
    "ExDividendDate": "2024-11-08",
    "PayoutRatio": "0.15",
    "Beta": "1.28",
    # Exotic / unknown — must survive passthrough.
    "AssetType": "Common Stock",
    # AV sentinels.
    "DividendDate": "None",
    "EVToRevenue": "-",
}


def test_parse_overview_extracts_top_level_and_coerces_numbers():
    top, fund = fx._parse_overview(SAMPLE)
    assert top["sector"] == "TECHNOLOGY"
    assert top["industry"] == "ELECTRONIC COMPUTERS"
    assert top["exchange"] == "NASDAQ"
    assert top["cik"] == "320193"
    assert top["name"] == "Apple Inc"
    assert fund["MarketCapitalization"] == 3_000_000_000_000.0
    assert fund["PERatio"] == 32.5
    assert fund["52WeekHigh"] == 199.62
    assert fund["SharesOutstanding"] == 15_000_000_000.0


def test_parse_overview_coerces_dates_and_rejects_sentinels():
    _, fund = fx._parse_overview(SAMPLE)
    assert isinstance(fund["ExDividendDate"], datetime)
    assert fund["ExDividendDate"].year == 2024
    # "None" / "-" sentinels become None.
    assert fund["DividendDate"] is None
    assert fund["EVToRevenue"] is None


def test_parse_overview_preserves_unknown_string_keys():
    _, fund = fx._parse_overview(SAMPLE)
    assert fund["AssetType"] == "Common Stock"


def test_looks_empty_catches_throttle():
    assert fx._looks_empty({}) is True
    assert fx._looks_empty({"Information": "rate limited"}) is True
    assert fx._looks_empty({"Note": "usage cap"}) is True
    assert fx._looks_empty({"Symbol": "AAPL", "Name": "Apple"}) is False
    # Bodies with only metadata keys and no real values are empty too.
    assert fx._looks_empty({"Symbol": "", "Name": ""}) is True


# -- Ingest dispatch -----------------------------------------------------


def _fake_client(**av_method_returns):
    c = MagicMock()
    for name, ret in av_method_returns.items():
        getattr(c, name).return_value = ret
    return c


def test_ingest_fundamentals_success_writes_fields():
    coll = MagicMock()
    with patch.object(fx.mongo, "companies", return_value=coll):
        client = _fake_client(overview=SAMPLE)
        r = fx.ingest_fundamentals("aapl", client=client)
    assert r.updated is True
    assert r.error is None
    assert r.fields_seen > 0
    filt, update = coll.update_one.call_args.args
    assert filt == {"symbol": "AAPL"}
    sets = update["$set"]
    assert sets["sector"] == "TECHNOLOGY"
    assert sets["symbol"] == "AAPL"
    assert "fundamentals" in sets
    assert sets["fundamentals"]["MarketCapitalization"] == 3e12
    assert coll.update_one.call_args.kwargs["upsert"] is True


def test_ingest_fundamentals_skips_empty_payload():
    coll = MagicMock()
    with patch.object(fx.mongo, "companies", return_value=coll):
        client = _fake_client(overview={"Information": "rate limited"})
        r = fx.ingest_fundamentals("AAPL", client=client)
    assert r.updated is False
    assert r.skipped_reason is not None
    assert coll.update_one.called is False


def test_ingest_fundamentals_captures_av_error():
    client = MagicMock()
    client.overview.side_effect = AlphaVantageError("quota exceeded")
    coll = MagicMock()
    with patch.object(fx.mongo, "companies", return_value=coll):
        r = fx.ingest_fundamentals("AAPL", client=client)
    assert r.error is not None
    assert "AV error" in r.error
    assert coll.update_one.called is False


# -- Fundamentals view ---------------------------------------------------


def test_load_fundamentals_view_with_data():
    doc = {
        "symbol": "AAPL",
        "name": "Apple Inc",
        "sector": "TECHNOLOGY",
        "industry": "ELECTRONIC COMPUTERS",
        "etf_memberships": ["SPY", "QQQ"],
        "description": "Designs things.",
        "fundamentals": {
            "MarketCapitalization": 3e12,
            "PERatio": 32.5,
            "ForwardPE": 29.8,
            "DividendYield": 0.0054,
            "ProfitMargin": 0.253,
            "52WeekHigh": 199.62,
            "SharesOutstanding": 15e9,
            "AnalystTargetPrice": 220.0,
        },
    }
    coll = MagicMock()
    coll.find_one.return_value = doc
    etf_coll = MagicMock()
    etf_coll.find_one.return_value = None
    with patch.object(queries.mongo, "companies", return_value=coll), \
         patch.object(queries.mongo, "etf", return_value=etf_coll):
        view = queries.load_fundamentals_view("AAPL")

    assert view.has_data is True
    assert view.name == "Apple Inc"
    assert view.etf_memberships == ["SPY", "QQQ"]
    # Identity includes sector value we set.
    identity = dict(view.identity)
    assert identity["Sector"] == "TECHNOLOGY"
    # Valuation numbers are formatted.
    valuation = dict(view.valuation)
    assert valuation["Market cap"].startswith("$3.00T")
    assert valuation["P/E (trailing)"] == "32.50"
    # Percent fields render with sign and % suffix.
    profit = dict(view.profitability)
    assert profit["Profit margin"].endswith("%")
    assert "25.30" in profit["Profit margin"]
    # Analyst integer rendering.
    analyst = dict(view.analyst)
    assert analyst["Target price"].startswith("$220.00")


def test_load_fundamentals_view_missing_doc_falls_back():
    coll = MagicMock()
    coll.find_one.return_value = None
    with patch.object(queries.mongo, "companies", return_value=coll), \
         patch.object(queries.mongo, "etf", return_value=MagicMock()):
        view = queries.load_fundamentals_view("NOPE")
    assert view.has_data is False
    # All section values are em-dashes (no data).
    assert all(v == "—" for _, v in view.identity)
    assert all(v == "—" for _, v in view.valuation)
