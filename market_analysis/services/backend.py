"""Array backend indirection.

All vectorized indicator math imports from here instead of importing
``numpy`` directly.  This is the Stage-3/4 seam: swapping between
NumPy (CPU) and MLX (Apple GPU/unified-memory) is a module-level
configuration change, not a call-site change.

Backend selection (at import time; first wins):
1. env var ``MARKET_ANALYSIS_BACKEND`` = ``"numpy"`` | ``"mlx"``
2. env var unset → ``"numpy"``

If ``"mlx"`` is requested but the ``mlx`` package is not importable,
we log a warning and silently fall back to NumPy so ordinary CI /
non-Apple environments keep working.

What callers may rely on:
- ``xp`` exposes ``asarray``/``array``, ``zeros``, ``full``,
  ``isnan``, ``where``, ``stack``, ``nonzero``, plus ``int32``/``bool``
  dtypes and the selected ``FLOAT`` dtype.
- ``NAN`` is a scalar that, broadcast with a ``FLOAT`` array, yields
  NaN in that array's dtype.
- ``as_array(data, dtype=...)`` is the universal constructor (MLX
  lacks ``asarray``; NumPy lacks some MLX-only signatures).
- ``to_numpy(arr)`` converts a backend array to a NumPy array at the
  I/O boundary (MongoDB writes, test assertions).  On NumPy it's a
  no-op.

What NOT to add here: any helper whose semantics differ between
backends silently.  If NumPy and MLX disagree (e.g. default float
precision), surface the difference via a named constant so callers
can reason about it.

Precision caveat: MLX is most efficient on ``float32``; NumPy's
default is ``float64``.  This module selects ``FLOAT`` accordingly.
Parity test tolerances must account for the chosen backend's
precision — see ``tests/test_indicators_parity.py``.
"""

from __future__ import annotations

import logging
import os

import numpy as _np

log = logging.getLogger(__name__)


_REQUESTED = os.environ.get("MARKET_ANALYSIS_BACKEND", "numpy").strip().lower()

# Default: NumPy.
xp = _np
FLOAT = _np.float64
INT = _np.int32
BOOL = _np.bool_
NAN = _np.nan
BACKEND_NAME = "numpy"

if _REQUESTED == "mlx":
    try:
        import mlx.core as _mx  # type: ignore[import-not-found]
        xp = _mx
        # MLX on Apple Silicon is optimized for float32; float64 falls back to CPU.
        FLOAT = _mx.float32
        INT = _mx.int32
        BOOL = _mx.bool_
        NAN = float("nan")
        BACKEND_NAME = "mlx"
        log.info("Array backend: MLX (float32, Apple GPU/unified memory)")
    except ImportError:
        log.warning(
            "MARKET_ANALYSIS_BACKEND=mlx requested but 'mlx' is not installed; "
            "falling back to NumPy.  Install with: pip install mlx"
        )
elif _REQUESTED not in ("numpy", ""):
    log.warning("Unknown MARKET_ANALYSIS_BACKEND=%r; using numpy.", _REQUESTED)


def as_array(data, *, dtype=None):
    """Universal array constructor.  NumPy ``asarray`` / MLX ``array``."""
    if BACKEND_NAME == "mlx":
        if dtype is None:
            return xp.array(data)
        return xp.array(data, dtype=dtype)
    return xp.asarray(data, dtype=dtype)


def to_numpy(arr) -> "_np.ndarray":
    """Convert a backend array to a NumPy array at the I/O boundary.

    Materializes any pending MLX computation via ``eval`` before
    conversion.  On NumPy this is effectively ``np.asarray(arr)``.
    """
    if BACKEND_NAME == "mlx":
        xp.eval(arr)
        return _np.asarray(arr)
    return _np.asarray(arr)
