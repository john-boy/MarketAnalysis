"""Tests for the pure-math indicator helpers.

Mongo-touching paths (``recompute_for_symbol``) are not tested here;
they're exercised in the end-to-end CLI smoke test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from market_analysis.services.indicators import (
    EMAStack,
    RSISpec,
    compute_ema_docs,
    compute_rsi_docs,
    ema,
    rsi,
)


# -- EMA ------------------------------------------------------------------


def test_ema_warmup_then_seeded():
    vals = [10.0, 11.0, 12.0, 13.0, 14.0]
    out = ema(vals, period=3)
    assert out[:2] == [None, None]
    # Seed = mean of first 3 = 11.0
    assert out[2] == pytest.approx(11.0)
    # Next step: alpha = 2/4 = 0.5 ; 0.5*13 + 0.5*11 = 12.0
    assert out[3] == pytest.approx(12.0)
    # Next: 0.5*14 + 0.5*12 = 13.0
    assert out[4] == pytest.approx(13.0)


def test_ema_too_short_returns_none():
    assert ema([1.0, 2.0], period=5) == [None, None]


def test_ema_rejects_bad_period():
    with pytest.raises(ValueError):
        ema([1.0], period=0)


# -- RSI ------------------------------------------------------------------


def test_rsi_all_gains_saturates_to_100():
    vals = [i * 1.0 for i in range(1, 20)]  # strictly increasing
    out = rsi(vals, period=14)
    # First 14 positions are None (period+1 seeded at index 14)
    assert out[:14] == [None] * 14
    # Pure up-moves → avg_loss = 0 → RSI = 100
    assert out[14] == pytest.approx(100.0)


def test_rsi_flat_is_100_by_convention():
    vals = [50.0] * 20
    out = rsi(vals, period=14)
    # No moves: avg_loss = 0 → RSI = 100 per our _rsi_from guard.
    assert out[-1] == pytest.approx(100.0)


def test_rsi_short_series_returns_none():
    assert rsi([1.0, 2.0, 3.0], period=14) == [None, None, None]


def test_rsi_rejects_bad_period():
    with pytest.raises(ValueError):
        rsi([1.0, 2.0], period=1)


# -- Doc shaping ----------------------------------------------------------


def test_compute_ema_docs_shape():
    dates = [datetime(2024, 1, i, tzinfo=timezone.utc) for i in range(1, 11)]
    closes = [100.0 + i for i in range(10)]
    docs = compute_ema_docs("AAPL", dates, closes, EMAStack(short=3, middle=5, long=7, stack=9))
    assert docs, "expected at least one doc"
    d0 = docs[0]
    assert d0["metadata"] == {
        "symbol": "AAPL",
        "indicator": "ema",
        "stack": 9,
        "params": {"short": 3, "middle": 5, "long": 7},
    }
    assert "short" in d0 and "middle" in d0 and "long" in d0
    assert d0["date"] in dates


def test_compute_rsi_docs_shape():
    dates = [datetime(2024, 1, i, tzinfo=timezone.utc) for i in range(1, 20)]
    closes = [100.0 + (i % 3) for i in range(19)]
    docs = compute_rsi_docs("AAPL", dates, closes, RSISpec(period=5, stack=2))
    assert docs
    d0 = docs[0]
    assert d0["metadata"]["indicator"] == "rsi"
    assert d0["metadata"]["stack"] == 2
    assert d0["metadata"]["params"] == {"period": 5}
    assert 0.0 <= d0["value"] <= 100.0
