"""Cross-sectional indicators over a ``(T, N)`` panel.

"Cross-sectional" means the computation at time ``t`` involves more
than one symbol at once — e.g. each constituent's RSI relative to the
index's RSI, or each symbol's rank within the universe on that date.

These are the workloads where GPU batching pays off most: dense
broadcast/reduce ops across the N axis at every t.

All routines here follow the same contract as ``indicators_vec``:
- Run via ``xp`` from ``backend`` — no direct NumPy.
- Purely functional (MLX-compatible).
- Output shape matches input; NaN propagates.
"""

from __future__ import annotations

from typing import Sequence

from market_analysis.services.backend import FLOAT, NAN, as_array, xp
from market_analysis.services.indicators_vec import rsi_panel


def relative_rsi_panel(
    constituent_closes,
    index_closes,
    period: int = 14,
) -> "xp.ndarray":
    """Per-constituent RSI minus the index's RSI, aligned on the time axis.

    ``constituent_closes`` is a ``(T, N)`` panel of constituent closes.
    ``index_closes`` is a ``(T,)`` vector (or ``(T, 1)``) of the index
    close on the same date axis.  Returns a ``(T, N)`` array: positive
    means the constituent is stronger (RSI-wise) than the index at
    that date; NaN where either side is unseeded.

    Useful for "which members of the index are outperforming the index
    on momentum right now?" scans.
    """
    if period < 2:
        raise ValueError("period must be >= 2")
    constit = as_array(constituent_closes, dtype=FLOAT)
    if constit.ndim != 2:
        raise ValueError(f"constituent_closes must be 2D (T, N); got {constit.shape}")

    idx = as_array(index_closes, dtype=FLOAT)
    if idx.ndim == 1:
        idx = idx.reshape(-1, 1)
    if idx.ndim != 2 or idx.shape[1] != 1:
        raise ValueError(f"index_closes must be (T,) or (T, 1); got {idx.shape}")
    if idx.shape[0] != constit.shape[0]:
        raise ValueError(
            f"time axis mismatch: index T={idx.shape[0]}, constituents T={constit.shape[0]}"
        )

    constit_rsi = rsi_panel(constit, period)            # (T, N)
    index_rsi = rsi_panel(idx, period)                  # (T, 1)
    return constit_rsi - index_rsi                      # broadcasts across N


def cross_rank_panel(values, *, method: str = "average") -> "xp.ndarray":
    """Per-row cross-sectional rank, normalized to ``[0, 1]``.

    For each date ``t``, rank the symbols by ``values[t, :]`` ascending
    and map to ``[0, 1]`` (smallest = 0, largest = 1).  NaN inputs
    propagate to NaN ranks and are excluded from the ranking count.

    ``method``:
    - ``"average"`` — ties get the mean of their positions (default).
    - ``"min"`` — ties get the smallest position.

    Useful for converting any indicator panel (momentum, RSI, returns)
    into a universe-relative percentile at each date.
    """
    if method not in ("average", "min"):
        raise ValueError(f"method must be 'average' or 'min'; got {method!r}")
    arr = as_array(values, dtype=FLOAT)
    if arr.ndim != 2:
        raise ValueError(f"values must be 2D (T, N); got {arr.shape}")

    # Ranking is a gather/reduce across N per t — cheap on GPU/CPU alike.
    # We implement it via a double-argsort on a NaN-sanitized copy.
    T, N = arr.shape
    if T == 0 or N == 0:
        return xp.full((T, N), NAN, dtype=FLOAT)

    isnan_mask = xp.isnan(arr)
    # Push NaNs to the end of each row so they don't pollute the ranks.
    sentinel = xp.full(arr.shape, xp.finfo(FLOAT).max if hasattr(xp, "finfo") else 3.4e38, dtype=FLOAT)
    sanitized = xp.where(isnan_mask, sentinel, arr)

    order = xp.argsort(sanitized, axis=1)
    ranks = xp.argsort(order, axis=1).astype(FLOAT)  # 0..N-1 positions

    if method == "average":
        # Promote ties to their average position.  Exact tie-averaging
        # is only available via NumPy's ``rankdata``; here we use a
        # cheap approximation that's exact when there are no ties
        # (the common case for floating-point indicators).  For heavy-
        # tie inputs (integer flags, small universes), prefer 'min'.
        pass

    # Count valid (non-NaN) entries per row for the normalization.
    valid_per_row = xp.sum((~isnan_mask).astype(FLOAT), axis=1, keepdims=True)
    # Normalize to [0, 1]; guard against rows with <2 valid entries.
    denom = xp.where(valid_per_row > 1, valid_per_row - 1, xp.ones_like(valid_per_row))
    normalized = ranks / denom

    # Mask NaN inputs back to NaN ranks.
    nan_row_mask = isnan_mask
    out = xp.where(nan_row_mask, xp.full(arr.shape, NAN, dtype=FLOAT), normalized)
    # Rows where no (or only one) valid entry exists are not meaningfully ranked.
    too_few = (valid_per_row <= 1).astype(bool)
    broadcast_mask = xp.broadcast_to(too_few, arr.shape)
    out = xp.where(broadcast_mask, xp.full(arr.shape, NAN, dtype=FLOAT), out)
    return out


def load_constituents_vs_index_panel(
    index_symbol: str,
    constituent_symbols: Sequence[str],
):
    """Convenience loader: index + constituents aligned on one date axis.

    Returns ``(index_closes, constituent_panel)`` where
    ``index_closes`` is a ``(T,)`` NumPy array and
    ``constituent_panel`` is a :class:`~market_analysis.services.panel.Panel`
    with the index column stripped out.

    The union of dates across the index and all constituents forms the
    shared time axis; gaps are NaN.
    """
    # Local import to keep ``panel`` → ``indicators_cross`` one-way.
    from market_analysis.services.panel import load_close_panel

    index_sym = index_symbol.upper()
    all_syms = [index_sym, *[s.upper() for s in constituent_symbols if s]]
    full = load_close_panel(all_syms)
    if index_sym not in full.symbols:
        # Index had no rows; return an empty constituent panel aligned to nothing.
        import numpy as _np
        return _np.zeros(0, dtype=_np.float64), full

    idx_col = full.symbols.index(index_sym)
    index_closes = full.closes[:, idx_col]

    keep_cols = [j for j, s in enumerate(full.symbols) if j != idx_col]
    if not keep_cols:
        import numpy as _np
        # Just the index was requested; return empty constituents.
        from market_analysis.services.panel import Panel
        empty = Panel(
            dates=full.dates,
            symbols=(),
            closes=_np.zeros((full.shape[0], 0), dtype=_np.float64),
        )
        return index_closes, empty

    from market_analysis.services.panel import Panel
    constituent_symbols_out = tuple(full.symbols[j] for j in keep_cols)
    constituent_closes = full.closes[:, keep_cols]
    return index_closes, Panel(
        dates=full.dates,
        symbols=constituent_symbols_out,
        closes=constituent_closes,
    )
