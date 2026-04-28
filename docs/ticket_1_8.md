## Ticket 1.8 — Batched indicator engine + GPU backend seam ✅

Re-engineering of the indicator calculation pipeline from per-symbol
Python loops to batched tensor math across all symbols, with a
pluggable array backend so Apple-GPU acceleration (MLX) can be
toggled on without touching call sites.  Motivated by the project's
ultimate goal of mining the MongoDB corpus for market opportunities
via cross-sectional mathematics, where per-symbol loops don't scale.

### What's new

**`market_analysis/services/backend.py`** — array-backend indirection.
- Re-exports the active array module as ``xp`` along with the
  ``FLOAT`` / ``INT`` / ``BOOL`` dtypes and a NaN scalar.
- Backend selection: env var ``MARKET_ANALYSIS_BACKEND`` = ``numpy``
  (default) or ``mlx``.  If ``mlx`` is requested but not installed,
  falls back to NumPy with a log warning — CI and non-Apple
  environments keep working.
- ``as_array(data, dtype=…)`` — universal constructor bridging NumPy
  ``asarray`` and MLX ``array``.
- ``to_numpy(arr)`` — materializes MLX lazy results and returns a
  NumPy array for the I/O boundary.

**`market_analysis/services/indicators_vec.py`** — vectorized per-
symbol indicators over a dense ``(T, N)`` panel.
- ``ema_panel(closes, period)`` and ``rsi_panel(closes, period)``
  compute the same math as the scalar implementations but for every
  symbol in lockstep along the time axis.
- Purely functional (no slice assignment) — required because
  ``mlx.core.array`` is immutable.  Output rows are built per step
  as fresh arrays and stacked at the end.
- NaN-safe: pre-history and missing bars propagate as NaN without
  corrupting the per-column recurrences.

**`market_analysis/services/indicators_cross.py`** — cross-sectional
primitives (Stage 4 of the re-engineering).
- ``relative_rsi_panel(constituents, index, period)`` — each
  constituent's RSI minus the index's RSI on a shared date axis.
- ``cross_rank_panel(values)`` — per-row percentile rank of an
  indicator panel, NaN-aware; turns any indicator into a universe-
  relative percentile.
- ``load_constituents_vs_index_panel(index, constituents)`` —
  convenience loader returning ``(index_closes, constituent_panel)``
  aligned on the union date axis.

**`market_analysis/services/panel.py`** — bulk I/O for the batched
pipeline.
- ``load_close_panel(symbols)`` — one Mongo read per pipeline run
  instead of N.  Returns a ``Panel`` dataclass: shared ``dates``
  axis, column-ordered ``symbols`` tuple, and a dense NumPy
  ``closes`` matrix with NaN for missing cells.
- ``write_indicator_panel(panel, indicator, stack, params, fields,
  mode)`` — bulk write: single ``delete_many`` + single
  ``insert_many`` per (indicator, stack) in ``full`` mode;
  per-symbol append-only via one aggregation for last-dates in
  ``auto`` mode.  Doc shape unchanged; ``mode="auto"`` semantics
  preserved.
- ``recompute_panel(symbols, …)`` — orchestrator that replaces the
  per-symbol ``recompute_for_symbol`` loop.

**`market_analysis/services/ingestors/daily.py`** — the "[4/4]
indicators" step now makes a single ``recompute_panel(symbols,
mode="auto")`` call instead of looping ``recompute_for_symbol``
across 200–300 symbols.  Progress reporting moves from per-symbol
to one-line panel summary.

**`run-numpy.sh` / `run-mlx.sh`** — executable launchers at the repo
root that set ``MARKET_ANALYSIS_BACKEND`` and invoke
``.venv/bin/python -m market_analysis.app.main``.  Extra args are
forwarded.

**`pyproject.toml`** — ``numpy>=2.0`` added as a required dep;
``mlx>=0.18`` added as an optional ``gpu`` extra gated on
``platform_system == 'Darwin' and platform_machine == 'arm64'``.

### Parity + tests

**`tests/test_indicators_parity.py`** — 16 tests that assert the
batched output matches the scalar ``ema()`` / ``rsi()`` ground truth
to 1e-9 (EMA) / 1e-6 (RSI) on synthetic random walks, multi-column
panels, and NaN-gap scenarios.  The scalar implementations in
``indicators.py`` remain the parity reference; the batched code must
not drift from them.

**`tests/test_indicators_cross.py`** — 9 tests covering
``relative_rsi_panel``, ``cross_rank_panel``, and an MLX end-to-end
smoke test (``@pytest.mark.skipif`` when MLX isn't installed) that
runs EMA + RSI through the MLX backend and compares the NumPy
round-trip to the NumPy reference within float32 tolerance.

All 83 tests pass; no existing test was modified.

### Architectural invariants introduced

- **Math never imports NumPy directly.**  Any vectorized indicator
  module must import from ``services.backend``.  This is the contract
  that keeps the MLX drop-in a one-line flip.
- **Math is purely functional.**  No slice assignment, no in-place
  mutation.  Accumulate output rows and ``xp.stack`` at the end.
- **I/O layer (``panel.py``) is pure NumPy.**  Backend arrays are
  converted via ``to_numpy()`` at the Mongo boundary so document
  serialization never depends on the active backend.
- **MLX runs at float32.**  Apple Silicon's GPU is optimized for
  float32; float64 falls back to CPU.  Parity tolerances for MLX
  outputs are looser than for NumPy reference (documented in the
  cross-sectional test file).

### Usage

```bash
# NumPy (default) — everyday development, CI, tests:
./run-numpy.sh

# MLX (Apple GPU) — for heavy cross-sectional workloads:
./run-mlx.sh
```

The app's observable behavior is identical under either backend for
current workloads.  MLX's decisive wins are expected on future
cross-sectional computations (rolling correlation matrices,
universe-wide scans, factor models) where the N-axis work dominates.

### What was not changed

- On-disk document shape in ``indicators`` collection.
- ``mode="auto"`` append-vs-refresh semantics (preserved byte-for-byte).
- Scalar ``recompute_for_symbol`` — retained for the Admin-tab single-
  symbol refresh path and existing tests.
- ETF / price ingest, UI widgets, poller.

### Follow-ups

- Benchmark MLX vs NumPy on real data at current scale (300 symbols
  × 6k bars).  Expectation: comparable, because GPU kernel-launch
  overhead cancels the compute win at this size.
- Promote MLX when a real cross-sectional workload ships (correlation
  matrix, index-relative scan on the full S&P 500).
- Memory settings deserve attention if the universe grows to the
  Russell 3000 or intraday frequencies — a panel of 3000 symbols ×
  25 years of 1-minute bars would be ~60 GB.
