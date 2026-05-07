"""Tests for the Extractor model and the extractor service.

No live Mongo / AV calls — the service-level tests stub the
``mongo`` collection accessors with in-memory fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from market_analysis.data.models import Extractor
from market_analysis.services import extractors as ex


# -- Model ----------------------------------------------------------------


def _start() -> datetime:
    return datetime(2020, 1, 1, tzinfo=timezone.utc)


def test_etf_kind_requires_etf_symbol():
    with pytest.raises(ValueError, match="etf_symbol"):
        Extractor(name="bad", kind="etf", start_date=_start())


def test_rotation_kind_requires_symbols():
    with pytest.raises(ValueError, match="symbols"):
        Extractor(name="bad", kind="rotation", start_date=_start())


def test_etf_kind_ok():
    rec = Extractor(name="x", kind="etf", etf_symbol="XLF", start_date=_start())
    assert rec.kind == "etf"
    assert rec.etf_symbol == "XLF"
    assert rec.symbols == []


def test_rotation_kind_ok():
    rec = Extractor(
        name="rot", kind="rotation",
        symbols=["XLB", "XLC"], start_date=_start(),
    )
    assert rec.symbols == ["XLB", "XLC"]


# -- resolve_symbols -----------------------------------------------------


class _FakeColl:
    def __init__(self, doc=None, rows=None):
        self._doc = doc
        self._rows = rows or []

    def find_one(self, *_args, **_kwargs):
        return self._doc

    def find(self, query, projection=None):
        sym = query["metadata.symbol"]
        return _FakeCursor([r for r in self._rows if r["metadata"]["symbol"] == sym])

    def update_one(self, *_a, **_k):
        return SimpleNamespace(upserted_id=None)


class _FakeCursor:
    def __init__(self, items):
        self._items = items

    def sort(self, *_a, **_k):
        return self

    def __iter__(self):
        return iter(self._items)


def test_resolve_symbols_etf_kind():
    rec = Extractor(name="x", kind="etf", etf_symbol="XLF", start_date=_start())
    etf_doc = {
        "symbol": "XLF",
        "holdings": [
            {"symbol": "JPM", "weight": 0.12},
            {"symbol": "BAC", "weight": 0.07},
            {"symbol": "JPM", "weight": 0.99},  # dup — first wins
        ],
    }
    with patch.object(ex.mongo, "etf", return_value=_FakeColl(doc=etf_doc)):
        pairs = ex.resolve_symbols(rec)
    # SPY first, then ETF, then deduped holdings; weights only on constituents.
    assert pairs == [
        ("SPY", None),
        ("XLF", None),
        ("JPM", 0.12),
        ("BAC", 0.07),
    ]


def test_resolve_symbols_rotation_kind():
    rec = Extractor(
        name="rot", kind="rotation",
        symbols=["XLB", "spy", "XLC"],  # SPY mixed-case → folded
        start_date=_start(),
    )
    pairs = ex.resolve_symbols(rec)
    assert pairs == [("SPY", None), ("XLB", None), ("XLC", None)]


# -- extract -------------------------------------------------------------


def test_extract_builds_rows_with_columns():
    rec = Extractor(
        name="rot", kind="rotation",
        symbols=["XLB"], start_date=_start(),
    )
    quote_rows = [
        {
            "date": datetime(2020, 1, 2, tzinfo=timezone.utc),
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
            "adjusted_close": 1.4, "volume": 1000.0, "candle": 0.1,
            "metadata": {"symbol": "SPY"},
        },
        {
            "date": datetime(2020, 1, 2, tzinfo=timezone.utc),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "adjusted_close": 10.4, "volume": 500.0, "candle": -0.2,
            "metadata": {"symbol": "XLB"},
        },
    ]
    quotes_coll = _FakeColl(rows=quote_rows)
    extractors_coll = _FakeColl(doc=rec.to_mongo())

    with patch.object(ex.mongo, "extractors", return_value=extractors_coll), \
         patch.object(ex.mongo, "daily_quotes", return_value=quotes_coll):
        rows, report = ex.extract("rot")

    assert report.total_rows == 2
    assert report.symbols == ["SPY", "XLB"]
    assert report.missing_symbols == []
    assert {r["symbol"] for r in rows} == {"SPY", "XLB"}
    assert all("extractor" not in r for r in rows)
    assert all(r["weight"] is None for r in rows)  # rotation kind → no weights
    assert all(set(r) >= set(ex.COLUMNS) for r in rows)


def test_extract_records_missing_symbols():
    rec = Extractor(
        name="rot", kind="rotation",
        symbols=["XLB"], start_date=_start(),
    )
    quotes_coll = _FakeColl(rows=[])
    extractors_coll = _FakeColl(doc=rec.to_mongo())
    with patch.object(ex.mongo, "extractors", return_value=extractors_coll), \
         patch.object(ex.mongo, "daily_quotes", return_value=quotes_coll):
        _, report = ex.extract("rot")
    assert report.total_rows == 0
    assert set(report.missing_symbols) == {"SPY", "XLB"}


def test_write_csv_header_and_weight(tmp_path):
    out = tmp_path / "out.csv"
    rows = [{
        "date": datetime(2020, 1, 2, tzinfo=timezone.utc),
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
        "adjusted_close": 1.4, "volume": 1000.0, "candle": 0.1,
        "symbol": "JPM", "weight": 0.12,
    }]
    p = ex.write_csv(rows, out)
    text = p.read_text()
    assert text.splitlines()[0] == (
        "date,volume,low,open,close,adjusted_close,high,candle,symbol,weight"
    )
    assert "0.12" in text
    assert "JPM" in text
    assert "extractor" not in text
