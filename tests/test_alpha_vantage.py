"""Tests for the Alpha Vantage adapter.

These tests never hit the network: we stub ``requests.Session.get``
with a fake that returns canned JSON.  They cover:

- typed methods pass the correct ``function``/``symbol`` params,
- the rate limiter blocks once its window is saturated,
- error envelopes (``Note``/``Information``/``Error Message``) raise,
- the disk cache returns the previous payload on replay (no second HTTP
  call), keyed independently of the API key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from market_analysis.sources.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageError,
    _RateLimiter,
)


def _fake_session(payload: Any) -> tuple[requests.Session, list[dict[str, Any]]]:
    """Return a Session whose ``get`` returns ``payload``; captured params."""
    captured: list[dict[str, Any]] = []

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        captured.append(dict(params or {}))
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = payload
        return resp

    sess = MagicMock(spec=requests.Session)
    sess.get.side_effect = fake_get
    return sess, captured


# -- Typed endpoints ------------------------------------------------------


def test_time_series_daily_adjusted_params():
    sess, captured = _fake_session({"Meta Data": {}, "Time Series (Daily)": {}})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    out = c.time_series_daily_adjusted("AAPL")
    assert "Time Series (Daily)" in out
    assert captured[0]["function"] == "TIME_SERIES_DAILY_ADJUSTED"
    assert captured[0]["symbol"] == "AAPL"
    assert captured[0]["outputsize"] == "full"
    assert captured[0]["apikey"] == "KEY"


def test_overview_params():
    sess, captured = _fake_session({"Symbol": "AAPL", "Name": "Apple"})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    out = c.overview("AAPL")
    assert out["Symbol"] == "AAPL"
    assert captured[0]["function"] == "OVERVIEW"


def test_etf_profile_params():
    sess, captured = _fake_session({"net_assets": "x", "holdings": []})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    c.etf_profile("SPY")
    assert captured[0]["function"] == "ETF_PROFILE"
    assert captured[0]["symbol"] == "SPY"


def test_earnings_params():
    sess, captured = _fake_session({"annualEarnings": [], "quarterlyEarnings": []})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    c.earnings("AAPL")
    assert captured[0]["function"] == "EARNINGS"


def test_news_sentiment_joins_iterables():
    sess, captured = _fake_session({"feed": []})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    c.news_sentiment(tickers=["AAPL", "MSFT"], topics=["technology"])
    p = captured[0]
    assert p["function"] == "NEWS_SENTIMENT"
    assert p["tickers"] == "AAPL,MSFT"
    assert p["topics"] == "technology"
    assert p["sort"] == "LATEST"
    assert p["limit"] == 50


# -- Error envelopes ------------------------------------------------------


@pytest.mark.parametrize("key", ["Error Message", "Information", "Note"])
def test_error_envelope_raises(key):
    sess, _ = _fake_session({key: "rate limited or bad symbol"})
    c = AlphaVantageClient(api_key="KEY", session=sess)
    with pytest.raises(AlphaVantageError):
        c.overview("XYZ")


def test_information_alongside_data_is_not_error():
    # A payload carrying Information plus real data shouldn't raise.
    payload = {
        "Information": "Note: adjusted close recomputed",
        "Meta Data": {},
        "Time Series (Daily)": {"2024-01-02": {}},
    }
    sess, _ = _fake_session(payload)
    c = AlphaVantageClient(api_key="KEY", session=sess)
    out = c.time_series_daily_adjusted("AAPL")
    assert "Time Series (Daily)" in out


def test_missing_api_key_raises():
    sess, _ = _fake_session({})
    c = AlphaVantageClient(api_key="", session=sess)
    with pytest.raises(AlphaVantageError):
        c.overview("AAPL")


# -- Rate limiter ---------------------------------------------------------


def test_rate_limiter_blocks_when_saturated(monkeypatch):
    # Zero min spacing so only the window cap applies in this test.
    rl = _RateLimiter(max_events=2, window_sec=60.0, min_spacing_sec=0.0)
    sleeps: list[float] = []
    # Simulate time frozen; record sleep calls and advance the clock by them.
    now = {"t": 1000.0}
    monkeypatch.setattr(
        "market_analysis.sources.alpha_vantage.time.monotonic",
        lambda: now["t"],
    )

    def fake_sleep(s):
        sleeps.append(s)
        now["t"] += s

    monkeypatch.setattr(
        "market_analysis.sources.alpha_vantage.time.sleep",
        fake_sleep,
    )

    rl.acquire()
    rl.acquire()
    rl.acquire()  # must wait roughly 60s
    assert sleeps, "expected at least one sleep call"
    assert sum(sleeps) >= 60.0


def test_rate_limiter_enforces_min_spacing(monkeypatch):
    # 75/min → default spacing = 60/75 = 0.8s between calls.
    rl = _RateLimiter(max_events=75, window_sec=60.0)
    now = {"t": 1000.0}
    sleeps: list[float] = []
    monkeypatch.setattr(
        "market_analysis.sources.alpha_vantage.time.monotonic",
        lambda: now["t"],
    )

    def fake_sleep(s):
        sleeps.append(s); now["t"] += s

    monkeypatch.setattr(
        "market_analysis.sources.alpha_vantage.time.sleep",
        fake_sleep,
    )
    rl.acquire()
    rl.acquire()
    # Second acquire happens immediately after the first; the limiter
    # must sleep ~0.8s to respect min-spacing.
    assert sum(sleeps) == pytest.approx(0.8, abs=0.01)


def test_rate_limiter_retries_after_429(monkeypatch):
    # Simulate a session that returns 429 once then 200.
    from unittest.mock import MagicMock
    import requests
    from market_analysis.sources.alpha_vantage import AlphaVantageClient

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):  # noqa: ANN001
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] == 1:
            resp.status_code = 429
            resp.headers = {"Retry-After": "1"}
            return resp
        resp.status_code = 200
        resp.headers = {}
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"Symbol": "AAPL"}
        return resp

    sess = MagicMock(spec=requests.Session)
    sess.get.side_effect = fake_get
    monkeypatch.setattr(
        "market_analysis.sources.alpha_vantage.time.sleep",
        lambda s: None,
    )
    c = AlphaVantageClient(api_key="KEY", session=sess)
    out = c.overview("AAPL")
    assert out == {"Symbol": "AAPL"}
    assert calls["n"] == 2


# -- Disk cache -----------------------------------------------------------


def test_cache_round_trip(tmp_path: Path):
    payload = {"Symbol": "AAPL", "Name": "Apple"}
    sess, captured = _fake_session(payload)
    c = AlphaVantageClient(
        api_key="KEY", session=sess, cache=True, cache_dir=tmp_path,
    )
    first = c.overview("AAPL")
    second = c.overview("AAPL")
    assert first == second == payload
    # Only one real HTTP call — second came from disk.
    assert len(captured) == 1


def test_cache_key_independent_of_api_key(tmp_path: Path):
    payload = {"Symbol": "AAPL"}
    sess_a, captured_a = _fake_session(payload)
    sess_b, captured_b = _fake_session(payload)
    c1 = AlphaVantageClient(api_key="KEY1", session=sess_a, cache=True, cache_dir=tmp_path)
    c2 = AlphaVantageClient(api_key="KEY2", session=sess_b, cache=True, cache_dir=tmp_path)
    c1.overview("AAPL")
    c2.overview("AAPL")  # expected to hit cache from c1
    assert len(captured_a) == 1
    assert len(captured_b) == 0
