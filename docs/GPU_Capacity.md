# GPU Capacity

## John
I did an initial commit to GitHub this morning so there would be a checkpoint.  I want to consider a pause to reengineer the calculation methodology.  My MAC has GPU capability.  Let's exploit this resource for lengthy calculations like the indicators.  My ultimate goal is to use this database to ferret out market opportunities using mathematics.  It would be wise to take advantage of the GPU capacity.  Consider this request and let me know what you think.

## Claude
On Apple Silicon, the practical GPU paths are MLX (Apple's native framework, best perf) or PyTorch with MPS backend — both can accelerate rolling-window indicators, matrix-heavy factor models, and backtests, but only pay off when you're processing many symbols/bars in parallel (think thousands of tickers × years of bars). The tradeoff: GPU shines on vectorized batch math but hurts you on small/iterative workloads due to CPU↔GPU transfer overhead, and it adds a dependency + debugging surface that pure NumPy/pandas doesn't have.

My recommendation: before re-engineering, let me look at your current indicator code and data volumes — if you're computing indicators one symbol at a time in Python loops, the bigger win is probably vectorizing across symbols first (NumPy/Polars), then layering MLX on the genuinely heavy steps (covariance matrices, optimization, ML features). Want me to audit the repo and come back with a sized recommendation?

## John
Yes, an audit is reasonable.  But as you perform the audit please consider potential optimization.  For instance, there will be several EMA indicators for each symbol.  Is it reasonable to send all of these to the GPU with a single raw data load?  In the future there will be the need to load index data and compute a comparative RSI across members of the index.  That is one example of many potential large computation tasks.  Consider these future needs as you audit the repo.

## Claude

## Audit Summary

**What you have:** Pure Python loops in [indicators.py](market_analysis/services/indicators.py) (EMA lines 65–84, RSI lines 87–113), one symbol at a time, orchestrated sequentially in [daily.py](market_analysis/services/ingestors/daily.py:155). MongoDB storage, ~200–300 symbols × ~6k daily bars each, no NumPy, no batching.

**Key finding:** You're not even vectorized yet on CPU. That actually makes the re-engineering *cleaner* — there's no legacy NumPy pipeline to untangle.

### Recommended direction (staged, not a big-bang rewrite)

**Stage 1 — restructure data flow (do this regardless of GPU):**
- Single bulk load: one Mongo query pulls all symbols' closes into a dense `(bars × symbols)` array, aligned on trading-day index. Missing bars → NaN.
- Single bulk write: batched Mongo upsert after all indicators compute.
- This alone fixes the per-symbol read/write round-trip cost, which likely dominates today.

**Stage 2 — vectorize math:**
- EMA across all symbols in parallel is a trivial recurrence on the column axis — one pass over the time axis, all symbols advance together. Multiple EMA periods (12/26/50) compute in the same pass from the same loaded tensor. ✅ exactly your intuition.
- RSI same pattern (Wilder smoothing is sequential in time but independent across symbols).

**Stage 3 — GPU backend (MLX recommended):**
- MLX is Apple-native, unified memory (no CPU↔GPU copy tax — big deal on M-series), and its API mirrors NumPy closely. PyTorch MPS works but has more transfer overhead and rougher edges.
- Wrap the math behind a thin `Backend` interface (`numpy` | `mlx`) so you can A/B test and fall back. For 300 symbols × 6k bars, CPU-vectorized NumPy may actually beat GPU due to kernel launch overhead — **GPU's real payoff is Stage 4.**

**Stage 4 — cross-sectional (where GPU decisively wins):**
- Index-relative RSI, rolling correlation matrices, beta surfaces, PCA/factor models across constituents — these are dense matrix ops (N × N × T). This is where MLX will crush CPU, and where your "ferret out opportunities via math" goal lives.

### Tradeoff to weigh
Stage 1+2 gets you maybe 20–50× speedup with zero GPU complexity. Stage 3 may be flat or slightly faster at current scale. Stage 4 is the real prize. So: **don't re-engineer *for* the GPU — re-engineer for batching, and the GPU becomes a drop-in backend when the workloads get heavy enough to need it.**

Want me to sketch the Stage 1 data-flow refactor (the bulk-load + tensor shape contract) as a concrete plan before any code changes?

## John

That's a reasonable plan.  I sense that the GPU acceleration will be very cost-effective in the near future.  Please include the "drop-in" as a requirement when we achieve Stage 4.  Proceed.

## Claude

Saving the architectural decision, then reading the actual indicator code before proposing Stage 1 specifics.

Code is cleaner than I feared — math is already isolated from I/O, and the incremental-vs-full `mode` logic is well-factored. That makes staging easy.

Before I write code, here's the concrete Stage 1+2 shape for your review. Stage 3 (backend abstraction) is deferred but its seams are designed-in now.

### Stage 1 — Data flow (new module, no math changes)

New module: `market_analysis/services/panel.py`

- `load_close_panel(symbols: list[str]) -> Panel` — **one** aggregation pipeline against `daily_quotes` that returns a dense `(T × N)` NumPy float32/64 matrix of adjusted closes, a shared `dates: np.ndarray[datetime64]` time axis, and the `symbols` column order. Missing bars → NaN; fallback `adjusted_close → close` preserved.
- `Panel` dataclass: `dates`, `symbols`, `closes` (2D array). Immutable, cheap to slice.
- `write_indicator_panel(indicator, stack, params, panel, values_by_field)` — bulk write: one `delete_many` by metadata + one `insert_many` per indicator, docs built by iterating the `(T × N)` result matrix and filtering NaN rows per symbol. Same doc shape as today — no schema change.

### Stage 2 — Vectorized math (`indicators_vec.py`)

- `ema_panel(closes: np.ndarray, period: int) -> np.ndarray` — forward recurrence along axis 0, all symbols advance in lockstep. NaN-safe (NaN propagates until enough real bars seeded per column).
- `rsi_panel(closes: np.ndarray, period: int) -> np.ndarray` — same pattern, Wilder smoothing along axis 0.
- `recompute_all(symbols, ema_specs, rsi_specs, mode)` — new entry point that replaces the per-symbol loop in [daily.py:155](market_analysis/services/ingestors/daily.py:155). Old `recompute_for_symbol` stays for now (Admin tab, tests) and internally can keep calling the scalar path until parity is verified.

### Stage 3 seam (designed in, not implemented)

All functions in `indicators_vec.py` take and return plain arrays and never import `numpy` directly — they import from an internal `market_analysis.services.backend` module that today re-exports `numpy as xp`. Swap to MLX later = change one import. No caller churn.

### Validation gate before cutover

Parity test: for a sample of ~10 symbols, assert new panel results match the current scalar `ema()`/`rsi()` outputs to within 1e-9 (EMA) / 1e-6 (RSI, due to float-op ordering). Only then flip `daily.py` to the batched path.

### What I will NOT touch in this pass
- Mongo schema / doc shape
- `ingest_prices`, ETF ingest, Qt workers
- The `mode="auto"` append-vs-refresh semantics (preserved)

### Open questions before I start
1. **NumPy as a new dependency** — is `numpy` already acceptable in `pyproject.toml`? (I'll check, but confirming you're OK adding it if absent.)
2. **Memory** — 300 symbols × 6k bars × 8 bytes ≈ 14 MB per panel. Negligible. If you later expand to intraday or full Russell 3000, it stays under 1 GB. Flagging for awareness, not a concern now.
3. **Test scope** — do you want me to add parity tests as part of this stage, or is a one-off validation script fine?

Confirm and I'll begin with Stage 1.

## John

I am not sure what you mean by parity tests versus one-off validation script.  Please explain.

## Claude

Both compare the new batched output against the current scalar output for the same inputs. The difference is where the check lives and how long it stays.

**Parity test** — a proper test file (e.g. `tests/test_indicators_parity.py`) that runs under `pytest` every time the suite runs. It fixes a handful of symbols' close series as inputs, calls both the old `ema()`/`rsi()` and the new `ema_panel()`/`rsi_panel()`, and asserts the arrays match within tolerance. Lives in the repo forever. Catches regressions years from now if someone "optimizes" the vectorized path and silently drifts. Cost: ~30 lines, runs in milliseconds.

**One-off validation script** — a throwaway file (e.g. `scripts/validate_panel.py`) I'd run once against your live Mongo data, print a diff report ("AAPL EMA-12 max abs error = 2.1e-12"), eyeball it, then delete. Never runs again. Cheaper to write (no fixture data needed — uses real DB), but gives no future protection.

**My recommendation:** parity test. The scalar implementations are the ground truth you already trust; locking that equivalence into CI is cheap insurance, and the fixture is trivial (synthetic price series or a few hundred bars of real data pasted into the test). The one-off is only attractive if you don't have a test harness set up — and you do.

Want me to proceed with parity tests baked in?

## John

Yes.  Proceed with the parity test included.

In answer to question 1 I have no problem adding numpy dependency.

## Claude

Adding numpy, then building the modules.

All 16 parity tests pass. Running the full suite to confirm nothing regressed before wiring `daily.py`.

One unrelated failure (missing `pyqtgraph` in env). All 74 other tests pass, including 16 new parity tests.

Wiring `daily.py` to the batched path.

Stages 1+2 complete. All 74 tests pass (16 new parity tests + 58 existing).

### What landed
- [backend.py](market_analysis/services/backend.py) — Stage 3 seam; re-exports `numpy as xp`. Future MLX swap = change one module.
- [indicators_vec.py](market_analysis/services/indicators_vec.py) — `ema_panel()`, `rsi_panel()`. NaN-safe, vectorized across symbols, parity-matched to scalar.
- [panel.py](market_analysis/services/panel.py) — `load_close_panel()` (single Mongo read → dense `(T, N)` tensor), `write_indicator_panel()` (single delete + insert per indicator/stack), `recompute_panel()` orchestrator.
- [tests/test_indicators_parity.py](tests/test_indicators_parity.py) — 16 tests asserting batched output matches scalar to 1e-9 (EMA) / 1e-6 (RSI) on synthetic walks and multi-column panels, plus NaN-gap handling.
- [daily.py:151](market_analysis/services/ingestors/daily.py:151) — the per-symbol indicator loop is replaced by a single `recompute_panel()` call. Scalar `recompute_for_symbol` left intact for the Admin tab and other callers.
- [pyproject.toml](pyproject.toml) — `numpy>=2.0` added.

### What's unchanged
- Mongo doc shape, `mode="auto"` append semantics, ETF/price ingest, UI.
- Old scalar path still works; nothing depends on the batched path except `daily.py`.

### Unrelated: one pre-existing test failure in the environment
`tests/test_app_imports.py` fails because `pyqtgraph` isn't installed in this Python env. Not caused by this work.

### Next up (when you're ready)
Stage 4 — cross-sectional work (index-relative RSI, correlation matrices) and the MLX drop-in. The backend seam is in place; swapping `xp = numpy` to `xp = mlx.core` will be a one-line change once we validate MLX performance on your hardware.

## John

Proceed

## Claude

Stage 4 scope: (a) refactor math to be MLX-compatible (no in-place writes, since MLX arrays are immutable), (b) add MLX as a selectable backend with graceful fallback, (c) cross-sectional module with index-relative RSI as the first entry.

Simplifying the scalar handling — `FLOAT` will be a dtype (not callable in MLX), so I should use Python floats with `dtype=FLOAT` everywhere.

Now the cross-sectional module and helpers.

24 pass, 1 skipped (MLX test, since MLX isn't installed yet). Let me try installing MLX to verify the backend swap actually works end-to-end.

MLX backend test passes — the drop-in works end-to-end on real hardware. Running full suite.

Stage 4 complete. 83 tests pass, including the MLX-backend end-to-end test that executed the math on Apple GPU and round-tripped results to NumPy within float32 tolerance.

### What landed
- [backend.py](market_analysis/services/backend.py) — backend selection via `MARKET_ANALYSIS_BACKEND=mlx`. Exports `xp`, `FLOAT`, `INT`, `BOOL`, `NAN`, `as_array()`, `to_numpy()`. Silent fallback to NumPy if MLX isn't installed.
- [indicators_vec.py](market_analysis/services/indicators_vec.py) — refactored to **purely functional** style (no slice assignment), compatible with MLX's immutable arrays. Parity with scalar math preserved.
- [indicators_cross.py](market_analysis/services/indicators_cross.py) — Stage-4 cross-sectional primitives:
  - `relative_rsi_panel(constituents, index, period)` — each constituent's RSI minus the index's RSI on a shared time axis.
  - `cross_rank_panel(values)` — per-row percentile rank of an indicator panel; NaN-aware. Useful for "who's in the top decile on momentum today?" scans.
  - `load_constituents_vs_index_panel(index, constituents)` — one-shot loader returning `(index_closes, constituent_panel)` aligned on the same dates.
- [panel.py](market_analysis/services/panel.py) — uses `to_numpy()` at the Mongo boundary, so math can run on any backend and docs always serialize from NumPy.
- [pyproject.toml](pyproject.toml) — `mlx>=0.18` added as an optional `gpu` extra, gated on Apple Silicon.
- [tests/test_indicators_cross.py](tests/test_indicators_cross.py) — 9 tests (relative RSI, cross rank, MLX end-to-end round-trip).

### How to use MLX
```bash
pip install 'market-analysis[gpu]'
MARKET_ANALYSIS_BACKEND=mlx python -m market_analysis ...
```
All existing call sites (`ema_panel`, `rsi_panel`, `relative_rsi_panel`, `cross_rank_panel`) transparently run on GPU. No code changes required.

### Known caveat
MLX defaults to float32 on GPU, so results differ from NumPy float64 by ~1e-3 (EMA) and up to ~0.5 RSI points on long recurrences. For the opportunity-mining use case this is well within tolerance; for anything requiring strict float64 parity, stick with the NumPy backend.

## John

I'm not clear what the How to use MLX section is telling me.  When do I use those instructions?  Is MLX not part of the normal python execution?

## Claude

Fair question — I buried the lede. Here's the clear version:

**MLX is opt-in, not automatic.** Two switches have to be flipped for MLX to run:

1. **Install it:** `pip install mlx` (or `pip install 'market-analysis[gpu]'`). Without this step, `import mlx` fails and the backend stays on NumPy no matter what you do. *In this environment I already installed it so the test could run — but it's not a required dependency of the project.*

2. **Select it at runtime:** set the environment variable `MARKET_ANALYSIS_BACKEND=mlx` before launching Python. Without this, [backend.py](market_analysis/services/backend.py) defaults to NumPy even if MLX is installed.

**Default behavior (both switches off):** NumPy on CPU. This is what you get when you run `python main.py` or `pytest` normally. It's what ran in all 83 tests except the one MLX-specific smoke test.

### When to actually flip the switches

Honestly — not yet, in most cases. At your current scale (300 symbols × 6k daily bars), NumPy is already fast enough that GPU kernel-launch overhead may cancel the compute win. The MLX drop-in is **infrastructure for later**, when you run:
- Large cross-sectional scans (`cross_rank_panel` across thousands of symbols)
- Rolling correlation matrices (N×N×T — quadratic in N)
- Any Monte Carlo, optimization, or ML-style workload you add for opportunity mining

For those, toggle MLX on and compare wall-clock. For daily indicator refresh on a few hundred symbols, NumPy is fine.

### What this means operationally

- Your daily pipeline (`daily_update`), Admin tab, tests, and IDE runs — all use NumPy automatically. No config needed.
- If you want to benchmark MLX, open a terminal and run:
  ```
  MARKET_ANALYSIS_BACKEND=mlx python -m market_analysis ...
  ```
- CI and non-Mac dev machines (if any) are unaffected — MLX isn't required to ship.

So: think of MLX as a feature flag for performance experiments and future heavy workloads, not a change to how you normally run the app.

## John

To be clear, to use MLX I would execute the two lines of code in question followed by .venv/bin/python -m market_analysis.app.main to use the app with MLX enabled?

## Claude

Close, but two refinements:

**1. Install is one-time, into your venv.** Use the venv's pip, not the system pip:
```
.venv/bin/pip install mlx
```
Do this once. It persists until you nuke `.venv/`.

**2. The env var has to be set for every launch.** Two equivalent forms:

Inline (applies to just that one command):
```
MARKET_ANALYSIS_BACKEND=mlx .venv/bin/python -m market_analysis.app.main
```

Or exported once per shell session (applies to every subsequent command in that terminal):
```
export MARKET_ANALYSIS_BACKEND=mlx
.venv/bin/python -m market_analysis.app.main
```

Without the env var, the app starts but [backend.py](market_analysis/services/backend.py) silently falls back to NumPy — you'd never know MLX was available. With it set, you'll see `Array backend: MLX (float32, Apple GPU/unified memory)` in the logs at startup (assuming logging is configured to show INFO).

If you want a quick way to confirm which backend is live without reading logs, I can add a one-line print at app startup — say the word.

## John

I installed mlx using PyCharm package manager.  Please add two scripts one for numpy execution and one for mlx.  This will make life simpler for me.

## Claude

Two executable launchers at the repo root:

- [run-numpy.sh](run-numpy.sh) — NumPy/CPU backend (default behavior, made explicit)
- [run-mlx.sh](run-mlx.sh) — MLX/Apple GPU backend

Both `cd` to the repo root, set `MARKET_ANALYSIS_BACKEND`, and launch the app via `.venv/bin/python`. Any extra args you pass are forwarded to the app.

Run from Terminal or from PyCharm (right-click → Run, or add as a shell-script Run Configuration):
```
./run-numpy.sh
./run-mlx.sh
```

