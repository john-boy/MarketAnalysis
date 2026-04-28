"""Tests for cross-sectional indicators and the MLX backend seam."""

from __future__ import annotations

import importlib
import math
import os

import numpy as np
import pytest

from market_analysis.services.indicators import rsi as scalar_rsi
from market_analysis.services.indicators_cross import (
    cross_rank_panel,
    relative_rsi_panel,
)


def _walk(seed: int, n: int, start: float = 100.0) -> list[float]:
    rng = np.random.default_rng(seed)
    return (start + np.cumsum(rng.normal(0.0, 1.0, size=n))).tolist()


# -- relative_rsi_panel ---------------------------------------------------


def test_relative_rsi_matches_scalar_difference():
    # Build an index and a few constituents; relative RSI should equal
    # constituent_rsi(t) - index_rsi(t) at every t where both are seeded.
    idx = _walk(seed=1, n=300, start=400.0)
    constituents = [_walk(seed=10 + i, n=300, start=100.0 + i) for i in range(5)]

    panel = np.asarray(constituents, dtype=np.float64).T  # (300, 5)
    idx_arr = np.asarray(idx, dtype=np.float64)

    out = np.asarray(relative_rsi_panel(panel, idx_arr, period=14))
    assert out.shape == (300, 5)

    idx_rsi = scalar_rsi(idx, period=14)
    for j, series in enumerate(constituents):
        const_rsi = scalar_rsi(series, period=14)
        for t in range(300):
            a, b, o = const_rsi[t], idx_rsi[t], out[t, j]
            if a is None or b is None:
                assert math.isnan(o)
            else:
                assert abs((a - b) - o) <= 1e-9


def test_relative_rsi_accepts_2d_index_column():
    idx = _walk(seed=2, n=100)
    constituents = [_walk(seed=20 + i, n=100) for i in range(3)]
    panel = np.asarray(constituents, dtype=np.float64).T
    idx_col = np.asarray(idx, dtype=np.float64).reshape(-1, 1)
    flat = np.asarray(relative_rsi_panel(panel, np.asarray(idx), period=14))
    wide = np.asarray(relative_rsi_panel(panel, idx_col, period=14))
    np.testing.assert_allclose(
        np.nan_to_num(flat, nan=-999.0),
        np.nan_to_num(wide, nan=-999.0),
    )


def test_relative_rsi_rejects_mismatched_T():
    with pytest.raises(ValueError):
        relative_rsi_panel(np.zeros((100, 3)), np.zeros(80), period=14)


def test_relative_rsi_rejects_bad_period():
    with pytest.raises(ValueError):
        relative_rsi_panel(np.zeros((20, 2)), np.zeros(20), period=1)


# -- cross_rank_panel -----------------------------------------------------


def test_cross_rank_basic():
    # Single-row panel with distinct values: smallest → 0.0, largest → 1.0.
    values = np.asarray([[10.0, 30.0, 20.0, 40.0]])
    out = np.asarray(cross_rank_panel(values))
    assert out.shape == (1, 4)
    # Rank positions: 10→0, 20→1, 30→2, 40→3 ; normalized by (N-1)=3.
    np.testing.assert_allclose(out[0], [0.0, 2 / 3, 1 / 3, 1.0], atol=1e-12)


def test_cross_rank_nan_propagates_and_excludes_from_count():
    # NaN in column 1 → that cell is NaN, and the remaining 3 values are
    # ranked among themselves (0, 0.5, 1).
    values = np.asarray([[10.0, np.nan, 20.0, 30.0]])
    out = np.asarray(cross_rank_panel(values))
    assert math.isnan(out[0, 1])
    np.testing.assert_allclose(
        [out[0, 0], out[0, 2], out[0, 3]], [0.0, 0.5, 1.0], atol=1e-12
    )


def test_cross_rank_single_valid_row_is_nan():
    # Only one non-NaN value → rank is undefined.
    values = np.asarray([[np.nan, 5.0, np.nan]])
    out = np.asarray(cross_rank_panel(values))
    assert np.all(np.isnan(out[0]))


def test_cross_rank_preserves_shape_across_rows():
    rng = np.random.default_rng(0)
    values = rng.normal(size=(50, 8))
    out = np.asarray(cross_rank_panel(values))
    assert out.shape == values.shape
    # Each row of ranks lies in [0, 1].
    assert np.nanmin(out) >= 0.0 - 1e-12
    assert np.nanmax(out) <= 1.0 + 1e-12


# -- MLX backend smoke test (skipped when mlx not installed) --------------


mlx_available = importlib.util.find_spec("mlx") is not None


@pytest.mark.skipif(not mlx_available, reason="mlx not installed")
def test_mlx_backend_produces_close_results():
    """End-to-end: under MARKET_ANALYSIS_BACKEND=mlx, the math routines
    must run and produce results within float32 tolerance of NumPy.

    Tolerance is loose because MLX defaults to float32 on Apple Silicon
    GPU; ~1e-3 absolute is the realistic bound for long RSI recurrences.
    """
    # Compute NumPy reference first.
    from market_analysis.services.indicators_vec import ema_panel as ema_np
    from market_analysis.services.indicators_vec import rsi_panel as rsi_np

    series = [_walk(seed=s, n=400, start=50 + s) for s in range(4)]
    panel = np.asarray(series, dtype=np.float64).T

    ref_ema = np.asarray(ema_np(panel, 26))
    ref_rsi = np.asarray(rsi_np(panel, 14))

    # Re-import under MLX.
    os.environ["MARKET_ANALYSIS_BACKEND"] = "mlx"
    try:
        import market_analysis.services.backend as backend_mod
        import market_analysis.services.indicators_vec as vec_mod
        importlib.reload(backend_mod)
        importlib.reload(vec_mod)
        assert backend_mod.BACKEND_NAME == "mlx", "MLX backend failed to activate"

        mlx_ema = backend_mod.to_numpy(vec_mod.ema_panel(panel, 26))
        mlx_rsi = backend_mod.to_numpy(vec_mod.rsi_panel(panel, 14))
    finally:
        # Restore NumPy for subsequent tests.
        os.environ.pop("MARKET_ANALYSIS_BACKEND", None)
        import market_analysis.services.backend as backend_mod
        import market_analysis.services.indicators_vec as vec_mod
        importlib.reload(backend_mod)
        importlib.reload(vec_mod)

    # Compare with float32-appropriate tolerance.
    np.testing.assert_allclose(
        np.nan_to_num(mlx_ema, nan=-1.0),
        np.nan_to_num(ref_ema, nan=-1.0),
        atol=1e-2, rtol=1e-3,
    )
    np.testing.assert_allclose(
        np.nan_to_num(mlx_rsi, nan=-1.0),
        np.nan_to_num(ref_rsi, nan=-1.0),
        atol=0.5, rtol=1e-2,
    )
