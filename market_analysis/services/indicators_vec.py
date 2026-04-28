"""Vectorized indicator math over a dense ``(T, N)`` panel.

One column per symbol, one row per trading date.  NaN means "no value
for this symbol at this date" — either the symbol's history hasn't
begun yet, or a bar is missing.  Every routine here is NaN-safe and
preserves exact parity with the scalar implementations in
``indicators.py`` for any column with no NaN gaps.

Design contract:
- All math runs via ``xp`` from ``backend`` — no direct ``numpy``.
- Time axis is 0; symbol axis is 1.
- Routines are **purely functional**: no slice assignment, no in-
  place mutation of arrays.  This keeps the MLX backend viable —
  ``mlx.core.array`` is immutable.  Output rows are built per time
  step as fresh arrays and stacked at the end.
- Output arrays have the same shape as the input; pre-seed positions
  and positions where input is NaN are NaN.
- Recurrences iterate over time (sequential) but are fully vectorized
  across symbols.  For N symbols of length T the cost is O(T) Python-
  level steps, each doing O(N) array work — the batching win.

Parity notes:
- EMA seed is the simple mean of the first ``period`` *valid* values
  per column.  Matches scalar ``ema()`` when the column has no NaN.
- RSI uses Wilder smoothing seeded from the first ``period`` changes
  between *consecutive valid* observations.  Matches scalar ``rsi()``
  when the column has no NaN.
"""

from __future__ import annotations

from market_analysis.services.backend import BOOL, FLOAT, INT, NAN, as_array, xp


def _as_float_panel(closes) -> "xp.ndarray":
    arr = as_array(closes, dtype=FLOAT)
    if arr.ndim != 2:
        raise ValueError(f"closes must be 2D (T, N); got shape {arr.shape}")
    return arr


def ema_panel(closes, period: int) -> "xp.ndarray":
    """Batched EMA along axis 0.

    For each column independently: skip NaN inputs, seed with the
    simple mean after ``period`` valid observations, then apply the
    standard recurrence ``prev = α·x + (1-α)·prev`` with α = 2/(N+1).
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    closes = _as_float_panel(closes)
    T, N = closes.shape
    if T == 0 or N == 0:
        return xp.full((T, N), NAN, dtype=FLOAT)

    alpha = 2.0 / (period + 1)
    one_minus_alpha = 1.0 - alpha

    valid_count = xp.zeros(N, dtype=INT)
    seed_sum = xp.zeros(N, dtype=FLOAT)
    prev = xp.zeros(N, dtype=FLOAT)
    seeded = xp.zeros(N, dtype=BOOL)

    zeros_row = xp.zeros(N, dtype=FLOAT)
    nan_row = xp.full(N, NAN, dtype=FLOAT)
    rows: list = []

    for t in range(T):
        row = closes[t]
        present = ~xp.isnan(row)
        # Treat NaN inputs as 0 for arithmetic; xp.where masks them out.
        row_safe = xp.where(present, row, zeros_row)

        # Post-seed recurrence.
        update = seeded & present
        prev = xp.where(update, alpha * row_safe + one_minus_alpha * prev, prev)

        # Warm-up accumulation toward the seed.
        warming = (~seeded) & present
        valid_count = xp.where(warming, valid_count + 1, valid_count)
        seed_sum = xp.where(warming, seed_sum + row_safe, seed_sum)
        just_seeded = warming & (valid_count == period)
        prev = xp.where(just_seeded, seed_sum / period, prev)
        seeded = seeded | just_seeded

        emit = update | just_seeded
        rows.append(xp.where(emit, prev, nan_row))

    return xp.stack(rows, axis=0)


def rsi_panel(closes, period: int = 14) -> "xp.ndarray":
    """Batched Wilder RSI along axis 0.

    For each column: take changes between consecutive *valid*
    observations; seed avg_gain/avg_loss from the first ``period``
    such changes; then apply Wilder smoothing.  Output is in ``0..100``
    with NaN before the seed is complete.
    """
    if period < 2:
        raise ValueError("period must be >= 2")
    closes = _as_float_panel(closes)
    T, N = closes.shape
    if T == 0 or N == 0:
        return xp.full((T, N), NAN, dtype=FLOAT)

    last_valid = xp.full(N, NAN, dtype=FLOAT)
    change_count = xp.zeros(N, dtype=INT)
    seed_gain = xp.zeros(N, dtype=FLOAT)
    seed_loss = xp.zeros(N, dtype=FLOAT)
    avg_gain = xp.zeros(N, dtype=FLOAT)
    avg_loss = xp.zeros(N, dtype=FLOAT)
    seeded = xp.zeros(N, dtype=BOOL)

    zeros_row = xp.zeros(N, dtype=FLOAT)
    nan_row = xp.full(N, NAN, dtype=FLOAT)
    ones_row = xp.full(N, 1.0, dtype=FLOAT)
    hundred_row = xp.full(N, 100.0, dtype=FLOAT)
    rows: list = []

    for t in range(T):
        row = closes[t]
        present = ~xp.isnan(row)
        has_prev = ~xp.isnan(last_valid)
        step = present & has_prev

        change = xp.where(step, row - xp.where(has_prev, last_valid, zeros_row), zeros_row)
        gain = xp.where(change > 0, change, zeros_row)
        loss = xp.where(change < 0, -change, zeros_row)

        post = step & seeded
        avg_gain = xp.where(post, (avg_gain * (period - 1) + gain) / period, avg_gain)
        avg_loss = xp.where(post, (avg_loss * (period - 1) + loss) / period, avg_loss)

        pre = step & (~seeded)
        change_count = xp.where(pre, change_count + 1, change_count)
        seed_gain = xp.where(pre, seed_gain + gain, seed_gain)
        seed_loss = xp.where(pre, seed_loss + loss, seed_loss)
        promote = pre & (change_count == period)
        avg_gain = xp.where(promote, seed_gain / period, avg_gain)
        avg_loss = xp.where(promote, seed_loss / period, avg_loss)
        seeded = seeded | promote

        emit = step & seeded
        loss_is_zero = avg_loss == 0
        safe_loss = xp.where(loss_is_zero, ones_row, avg_loss)
        rs = avg_gain / safe_loss
        rsi_val = xp.where(
            loss_is_zero,
            hundred_row,
            100.0 - 100.0 / (1.0 + rs),
        )
        rows.append(xp.where(emit, rsi_val, nan_row))

        last_valid = xp.where(present, row, last_valid)

    return xp.stack(rows, axis=0)
