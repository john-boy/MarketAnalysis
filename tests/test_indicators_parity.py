"""Parity tests: vectorized panel math vs. scalar ground truth.

The scalar implementations in ``indicators.py`` are the authoritative
reference.  Any divergence here is a regression in the batched path.
Tolerances are tight because the vectorized recurrences do the same
arithmetic in the same order (per column); the only slack is ordinary
float64 round-off.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from market_analysis.services.indicators import ema as scalar_ema
from market_analysis.services.indicators import rsi as scalar_rsi
from market_analysis.services.indicators_vec import ema_panel, rsi_panel


# -- Helpers --------------------------------------------------------------


def _synthetic_series(seed: int, n: int, start: float = 100.0) -> list[float]:
    """Deterministic pseudo-random walk; realistic enough for parity."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=0.0, scale=1.0, size=n)
    return (start + np.cumsum(steps)).tolist()


def _compare(scalar_out, vec_col, *, tol: float) -> None:
    """Assert element-wise agreement, treating ``None`` ↔ NaN."""
    assert len(scalar_out) == len(vec_col)
    for i, (s, v) in enumerate(zip(scalar_out, vec_col)):
        if s is None:
            assert math.isnan(v), f"index {i}: scalar=None but vec={v}"
        else:
            assert not math.isnan(v), f"index {i}: scalar={s} but vec=NaN"
            assert abs(s - v) <= tol, f"index {i}: scalar={s} vec={v} diff={s-v}"


# -- EMA parity -----------------------------------------------------------


@pytest.mark.parametrize("period", [3, 12, 26, 50])
def test_ema_parity_single_column(period):
    series = _synthetic_series(seed=42, n=500)
    panel = np.asarray(series, dtype=np.float64).reshape(-1, 1)
    vec = ema_panel(panel, period)
    scalar = scalar_ema(series, period)
    _compare(scalar, vec[:, 0], tol=1e-9)


def test_ema_parity_many_columns():
    # Stack 8 independent series as columns; each must match its own scalar.
    series_list = [_synthetic_series(seed=s, n=400, start=50 + s) for s in range(8)]
    panel = np.asarray(series_list, dtype=np.float64).T  # (400, 8)
    for period in (12, 26, 50):
        vec = ema_panel(panel, period)
        for j, s in enumerate(series_list):
            _compare(scalar_ema(s, period), vec[:, j], tol=1e-9)


def test_ema_too_short_all_nan():
    panel = np.asarray([[1.0], [2.0]], dtype=np.float64)
    out = ema_panel(panel, period=5)
    assert np.all(np.isnan(out))


def test_ema_rejects_bad_period():
    with pytest.raises(ValueError):
        ema_panel(np.zeros((10, 1)), period=0)


# -- RSI parity -----------------------------------------------------------


@pytest.mark.parametrize("period", [5, 14, 21])
def test_rsi_parity_single_column(period):
    series = _synthetic_series(seed=7, n=500)
    panel = np.asarray(series, dtype=np.float64).reshape(-1, 1)
    vec = rsi_panel(panel, period)
    scalar = scalar_rsi(series, period)
    # RSI involves more ops per step; allow a touch more slack.
    _compare(scalar, vec[:, 0], tol=1e-6)


def test_rsi_parity_many_columns():
    series_list = [_synthetic_series(seed=s + 100, n=300, start=40 + s) for s in range(6)]
    panel = np.asarray(series_list, dtype=np.float64).T
    vec = rsi_panel(panel, period=14)
    for j, s in enumerate(series_list):
        _compare(scalar_rsi(s, 14), vec[:, j], tol=1e-6)


def test_rsi_all_gains_saturates_to_100():
    series = [float(i) for i in range(1, 25)]  # strictly increasing
    panel = np.asarray(series, dtype=np.float64).reshape(-1, 1)
    out = rsi_panel(panel, period=14)
    assert math.isnan(out[13, 0])
    assert out[14, 0] == pytest.approx(100.0)


def test_rsi_flat_is_100_by_convention():
    series = [50.0] * 30
    panel = np.asarray(series, dtype=np.float64).reshape(-1, 1)
    out = rsi_panel(panel, period=14)
    assert out[-1, 0] == pytest.approx(100.0)


def test_rsi_rejects_bad_period():
    with pytest.raises(ValueError):
        rsi_panel(np.zeros((10, 1)), period=1)


# -- NaN handling (no scalar analogue — just shape/behavior) --------------


def test_ema_nan_gaps_preserved():
    # A column with NaNs should produce NaN at those rows and continue the
    # EMA recurrence over the remaining valid bars.
    col = _synthetic_series(seed=1, n=60)
    panel = np.asarray(col, dtype=np.float64).reshape(-1, 1)
    panel[5, 0] = np.nan
    panel[17, 0] = np.nan
    out = ema_panel(panel, period=5)
    assert math.isnan(out[5, 0])
    assert math.isnan(out[17, 0])
    # Plenty of valid post-seed rows remain.
    assert np.sum(~np.isnan(out[:, 0])) >= 50


def test_panel_columns_are_independent():
    # Inject NaN into column 0 only; column 1 must match its own scalar exactly.
    a = _synthetic_series(seed=11, n=200)
    b = _synthetic_series(seed=12, n=200)
    panel = np.asarray([a, b], dtype=np.float64).T
    panel[3, 0] = np.nan
    panel[50, 0] = np.nan
    out = ema_panel(panel, period=12)
    _compare(scalar_ema(b, 12), out[:, 1], tol=1e-9)
