"""Tests for incremental-mode price and indicator ingest.

These tests avoid Mongo by monkey-patching the collection accessors
to in-memory fakes; they verify the control flow (last-date lookup,
filter, append-only insert) rather than driver behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from market_analysis.services import indicators as ind_svc
from market_analysis.services.ingestors import prices as price_svc


# -- Fake collection -----------------------------------------------------


class FakeCollection:
    """Just enough of a pymongo Collection for the ingestors."""

    def __init__(self, docs: list[dict] | None = None) -> None:
        self.docs: list[dict] = list(docs or [])
        self.inserts: list[list[dict]] = []
        self.deletes: list[dict] = []

    def find_one(self, query, sort=None, projection=None):  # noqa: ANN001
        matches = [d for d in self.docs if _matches(d, query)]
        if sort:
            (key, order) = sort[0]
            matches.sort(key=lambda d: _dotted(d, key), reverse=(order == -1))
        return matches[0] if matches else None

    def delete_many(self, query):  # noqa: ANN001
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _matches(d, query)]
        self.deletes.append(query)
        return SimpleNamespace(deleted_count=before - len(self.docs))

    def insert_many(self, docs, ordered=False):  # noqa: ANN001, ARG002
        self.inserts.append(list(docs))
        self.docs.extend(docs)

    def find(self, query, projection=None):  # noqa: ANN001, ARG002
        matches = [d for d in self.docs if _matches(d, query)]
        return _Cursor(matches)


class _Cursor:
    def __init__(self, docs): self._docs = list(docs); self._sort_key = None
    def sort(self, key, order):  # noqa: ANN001
        self._docs.sort(key=lambda d: _dotted(d, key), reverse=(order == -1))
        return self
    def limit(self, n): self._docs = self._docs[:n]; return self
    def __iter__(self): return iter(self._docs)


def _dotted(d: dict, key: str):
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _matches(doc: dict, query: dict) -> bool:
    for k, v in query.items():
        actual = _dotted(doc, k)
        if isinstance(v, dict):
            # tiny operator subset
            for op, operand in v.items():
                if op == "$gt" and not (actual is not None and actual > operand):
                    return False
                elif op == "$ne" and actual == operand:
                    return False
        else:
            if actual != v:
                return False
    return True


# -- Fixtures ------------------------------------------------------------


@pytest.fixture
def fake_daily_quotes(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(
        "market_analysis.services.ingestors.prices.mongo.daily_quotes",
        lambda: fake,
    )
    return fake


@pytest.fixture
def fake_indicators(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(
        "market_analysis.services.indicators.mongo.indicators",
        lambda: fake,
    )
    return fake


@pytest.fixture
def fake_daily_quotes_for_indicators(monkeypatch):
    fake = FakeCollection()
    monkeypatch.setattr(
        "market_analysis.services.indicators.mongo.daily_quotes",
        lambda: fake,
    )
    return fake


# -- Price ingest: incremental path --------------------------------------


def _fake_av(payload):
    class _AV:
        def time_series_daily_adjusted(self, symbol, outputsize="full"):
            return payload
    return _AV()


def test_ingest_prices_auto_bootstraps_on_empty(fake_daily_quotes):
    payload = {"Time Series (Daily)": {
        "2024-01-03": {"4. close": "184.25", "5. adjusted close": "184.25"},
        "2024-01-02": {"4. close": "185.64", "5. adjusted close": "185.64"},
    }}
    rep = price_svc.ingest_prices("AAPL", client=_fake_av(payload), mode="auto")
    assert rep.mode == "full"            # bootstrapped
    assert rep.inserted == 2
    assert len(fake_daily_quotes.docs) == 2


def test_ingest_prices_auto_incremental_appends_only_new(fake_daily_quotes):
    # Seed: AAPL through 2024-01-02.
    fake_daily_quotes.docs = [
        {"date": datetime(2024, 1, 2, tzinfo=timezone.utc),
         "metadata": {"symbol": "AAPL"}, "close": 185.64},
    ]
    payload = {"Time Series (Daily)": {
        # Compact window with two dates; only 2024-01-03 is newer.
        "2024-01-03": {"4. close": "184.25", "5. adjusted close": "184.25"},
        "2024-01-02": {"4. close": "185.64", "5. adjusted close": "185.64"},
    }}
    rep = price_svc.ingest_prices("AAPL", client=_fake_av(payload), mode="auto")
    assert rep.mode == "incremental"
    assert rep.inserted == 1
    # No deletes in incremental mode.
    assert fake_daily_quotes.deletes == []
    # One insert batch, one doc.
    assert len(fake_daily_quotes.inserts) == 1
    assert len(fake_daily_quotes.inserts[0]) == 1


def test_ingest_prices_auto_noop_when_up_to_date(fake_daily_quotes):
    fake_daily_quotes.docs = [
        {"date": datetime(2024, 1, 3, tzinfo=timezone.utc),
         "metadata": {"symbol": "AAPL"}, "close": 184.25},
    ]
    payload = {"Time Series (Daily)": {
        "2024-01-03": {"4. close": "184.25", "5. adjusted close": "184.25"},
    }}
    rep = price_svc.ingest_prices("AAPL", client=_fake_av(payload), mode="auto")
    assert rep.mode == "incremental"
    assert rep.inserted == 0
    assert fake_daily_quotes.inserts == []


def test_ingest_prices_escalates_on_large_gap(monkeypatch, fake_daily_quotes):
    # Seed a very old last-date.
    fake_daily_quotes.docs = [
        {"date": datetime(2020, 1, 1, tzinfo=timezone.utc),
         "metadata": {"symbol": "AAPL"}, "close": 100.0},
    ]
    # AV returns a payload whose oldest bar is 2024 — a 4-year gap.
    compact_payload = {"Time Series (Daily)": {
        "2024-01-03": {"4. close": "184.25", "5. adjusted close": "184.25"},
    }}
    full_payload = {"Time Series (Daily)": {
        f"{y}-01-02": {"4. close": "100", "5. adjusted close": "100"}
        for y in range(2020, 2025)
    }}

    class _AV:
        def time_series_daily_adjusted(self, symbol, outputsize="full"):
            return compact_payload if outputsize == "compact" else full_payload

    rep = price_svc.ingest_prices("AAPL", client=_AV(), mode="auto")
    assert rep.escalated is True
    assert rep.mode == "full-escalated"
    # Delete-then-insert path taken.
    assert fake_daily_quotes.deletes


# -- Indicator engine: incremental path ----------------------------------


def test_recompute_auto_full_on_empty(
    fake_indicators, fake_daily_quotes_for_indicators,
):
    # Seed 60 quotes so EMA/RSI have room to converge.
    fake_daily_quotes_for_indicators.docs = [
        {"date": datetime(2024, 1, 1, tzinfo=timezone.utc).replace(day=(i % 28) + 1, month=(i // 28) + 1),
         "metadata": {"symbol": "AAPL"}, "close": 100.0 + i, "adjusted_close": 100.0 + i}
        for i in range(60)
    ]
    rep = ind_svc.recompute_for_symbol("AAPL", mode="auto")
    # At least one EMA & RSI bucket populated.
    assert any(k.startswith("ema/") and v > 0 for k, v in rep.counts.items())
    assert any(k.startswith("rsi/") and v > 0 for k, v in rep.counts.items())


def test_recompute_auto_appends_only_new_dates(
    fake_indicators, fake_daily_quotes_for_indicators,
):
    # 60 days of closes.
    quotes = [
        {"date": datetime(2024, 1, 1, tzinfo=timezone.utc).replace(day=(i % 28) + 1, month=(i // 28) + 1),
         "metadata": {"symbol": "AAPL"}, "close": 100.0 + i, "adjusted_close": 100.0 + i}
        for i in range(60)
    ]
    fake_daily_quotes_for_indicators.docs = quotes

    # First run: full.
    rep1 = ind_svc.recompute_for_symbol("AAPL", mode="full")
    ema_full = rep1.counts["ema/stack=0"]
    assert ema_full > 0
    seeded = list(fake_indicators.docs)

    # Clear delete/insert bookkeeping for a clean second-run check.
    fake_indicators.deletes.clear()
    fake_indicators.inserts.clear()

    # Second run on same quotes: incremental → no new rows.
    rep2 = ind_svc.recompute_for_symbol("AAPL", mode="auto")
    assert rep2.counts["ema/stack=0"] == 0
    # Append-only: no deletes on the second run.
    assert fake_indicators.deletes == []

    # Add one new quote day strictly later than any existing date.
    fake_daily_quotes_for_indicators.docs.append({
        "date": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "metadata": {"symbol": "AAPL"},
        "close": 200.0, "adjusted_close": 200.0,
    })
    rep3 = ind_svc.recompute_for_symbol("AAPL", mode="auto")
    assert rep3.counts["ema/stack=0"] == 1
    assert rep3.counts["rsi/stack=0"] == 1
    # Still append-only.
    assert fake_indicators.deletes == []
    # Previous rows untouched.
    assert all(d in fake_indicators.docs for d in seeded)
