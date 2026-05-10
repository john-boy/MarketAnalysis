"""Panel I/O: bulk load/write for batched indicator computation.

The batched pipeline replaces the per-symbol read → compute → write
loop with three phases:

1. ``load_close_panel(symbols)`` — a single Mongo read that returns a
   dense ``(T, N)`` matrix of adjusted closes aligned on a shared date
   axis.  Missing bars or pre-history are NaN.
2. Vectorized math in ``indicators_vec`` (axis 0 = time, axis 1 =
   symbol).  Same tensor in, same-shape tensor out.
3. ``write_indicator_panel(...)`` — a single delete + single insert
   per (indicator, stack) covering every symbol in the panel.

The on-disk document shape is unchanged — this module adapts between
the dense tensor view and the existing time-series doc format.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

from market_analysis.data import mongo
from market_analysis.services import indicators as ind_svc
from market_analysis.services.backend import to_numpy
from market_analysis.services.indicators import EMAStack, RSISpec

log = logging.getLogger(__name__)


# -- Panel container ------------------------------------------------------


@dataclass(frozen=True)
class Panel:
    """Dense close-price panel: ``closes[t, j]`` = close of ``symbols[j]`` on ``dates[t]``.

    ``dates`` is sorted ascending, unique across the union of every
    symbol's observed dates.  ``closes`` is a NumPy array (CPU) — the
    loader always produces NumPy; the active backend (NumPy or MLX)
    decides what array type math routines return when given this data.
    """

    dates: "np.ndarray"       # shape (T,), object dtype (datetime)
    symbols: tuple[str, ...]  # length N
    closes: "np.ndarray"      # shape (T, N), float64

    @property
    def shape(self) -> tuple[int, int]:
        return self.closes.shape


# -- Load -----------------------------------------------------------------


def load_close_panel(symbols: Sequence[str]) -> Panel:
    """Load a dense adjusted-close panel for ``symbols`` in one Mongo read.

    Adjusted close is preferred; ``close`` is the fallback when
    ``adjusted_close`` is missing or NaN (same rule the scalar loader
    uses).  Output column order matches ``symbols`` after uppercasing
    and dedup; empty symbols are dropped.
    """
    clean: list[str] = []
    seen: set[str] = set()
    for s in symbols:
        u = (s or "").upper()
        if u and u not in seen:
            clean.append(u)
            seen.add(u)
    if not clean:
        return Panel(
            dates=xp.asarray([], dtype="datetime64[ns]"),
            symbols=(),
            closes=xp.zeros((0, 0), dtype=FLOAT),
        )

    coll = mongo.price_history()
    cur = coll.find(
        {"metadata.symbol": {"$in": clean}},
        {"date": 1, "adj_close": 1, "adjusted_close": 1, "close": 1,
         "metadata.symbol": 1, "_id": 0},
    ).sort("date", 1)

    # Build per-symbol dict[date -> price] in one pass.
    by_sym: dict[str, dict[datetime, float]] = {s: {} for s in clean}
    all_dates: set[datetime] = set()
    for doc in cur:
        sym = doc["metadata"]["symbol"]
        if sym not in by_sym:
            continue
        price = doc.get("adj_close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            price = doc.get("adjusted_close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            price = doc.get("close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            continue
        d = doc["date"]
        by_sym[sym][d] = float(price)
        all_dates.add(d)

    sorted_dates = sorted(all_dates)
    T = len(sorted_dates)
    N = len(clean)
    closes = np.full((T, N), np.nan, dtype=np.float64)
    date_idx = {d: i for i, d in enumerate(sorted_dates)}
    for j, sym in enumerate(clean):
        col = by_sym[sym]
        for d, v in col.items():
            closes[date_idx[d], j] = v

    dates_arr = np.asarray(sorted_dates, dtype=object)
    return Panel(dates=dates_arr, symbols=tuple(clean), closes=closes)


# -- Write ----------------------------------------------------------------


def _last_indicator_dates(
    symbols: Sequence[str], indicator: str, stack: int
) -> dict[str, datetime]:
    """Return ``{symbol: max(date)}`` for the given indicator/stack.

    One aggregation instead of N per-symbol queries.  Symbols with no
    prior indicator docs are absent from the returned dict.
    """
    if not symbols:
        return {}
    cur = mongo.indicators().aggregate([
        {"$match": {
            "metadata.symbol": {"$in": list(symbols)},
            "metadata.indicator": indicator,
            "metadata.stack": stack,
        }},
        {"$group": {"_id": "$metadata.symbol", "last": {"$max": "$date"}}},
    ])
    return {doc["_id"]: doc["last"] for doc in cur}


def _build_docs(
    panel: Panel,
    indicator: str,
    stack: int,
    params: dict[str, Any],
    fields: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Convert a set of result arrays into per-symbol doc lists.

    ``fields`` maps the output field name (e.g. ``"short"``, ``"value"``)
    to a ``(T, N)`` array aligned with ``panel``.  Arrays may come
    from any backend (NumPy or MLX); they're converted to NumPy here
    — this is the I/O boundary.  A row is emitted for a given
    ``(t, j)`` iff at least one field is non-NaN there.
    """
    T, N = panel.shape
    field_names = list(fields.keys())
    np_fields = {k: to_numpy(fields[k]) for k in field_names}
    stacked = np.stack([np_fields[k] for k in field_names], axis=0)  # (F, T, N)
    any_valid = ~np.all(np.isnan(stacked), axis=0)  # (T, N)

    docs_by_symbol: dict[str, list[dict[str, Any]]] = {s: [] for s in panel.symbols}
    dates = panel.dates
    for j, sym in enumerate(panel.symbols):
        rows = np.nonzero(any_valid[:, j])[0]
        sym_docs = docs_by_symbol[sym]
        meta = {
            "symbol": sym,
            "indicator": indicator,
            "stack": stack,
            "params": params,
        }
        for t in rows.tolist():
            doc: dict[str, Any] = {"date": dates[t], "metadata": meta}
            for k, arr in np_fields.items():
                v = float(arr[t, j])
                doc[k] = None if math.isnan(v) else v
            sym_docs.append(doc)
    return docs_by_symbol


def write_indicator_panel(
    panel: Panel,
    indicator: str,
    stack: int,
    params: dict[str, Any],
    fields: dict[str, Any],
    *,
    mode: str = "auto",
) -> dict[str, int]:
    """Write one indicator's result panel back to Mongo.

    ``mode="full"``: one ``delete_many`` across every symbol in the
    panel for this (indicator, stack), then one ``insert_many``.
    ``mode="auto"``: per symbol, keep only rows newer than the symbol's
    current max date; no delete.  Returns the per-symbol insert counts.
    """
    if mode not in ("auto", "full"):
        raise ValueError(f"Unknown mode: {mode!r}")
    docs_by_symbol = _build_docs(panel, indicator, stack, params, fields)
    coll = mongo.indicators()
    counts: dict[str, int] = {}

    if mode == "full":
        coll.delete_many({
            "metadata.indicator": indicator,
            "metadata.stack": stack,
            "metadata.symbol": {"$in": list(panel.symbols)},
        })
        all_docs: list[dict[str, Any]] = []
        for sym, docs in docs_by_symbol.items():
            counts[sym] = len(docs)
            all_docs.extend(docs)
        if all_docs:
            coll.insert_many(all_docs, ordered=False)
        return counts

    # auto: per-symbol append-only
    last_by_sym = _last_indicator_dates(panel.symbols, indicator, stack)
    to_insert: list[dict[str, Any]] = []
    for sym, docs in docs_by_symbol.items():
        last = last_by_sym.get(sym)
        if last is None:
            new_docs = docs  # bootstrap: write full history
        else:
            new_docs = [d for d in docs if d["date"] > last]
        counts[sym] = len(new_docs)
        to_insert.extend(new_docs)
    if to_insert:
        coll.insert_many(to_insert, ordered=False)
    return counts


# -- Orchestrator ---------------------------------------------------------


@dataclass
class PanelReport:
    symbols: int = 0
    bars: int = 0
    counts: dict[str, dict[str, int]] = None  # {indicator_stack_key: {symbol: n}}

    def total_inserted(self) -> int:
        if not self.counts:
            return 0
        return sum(sum(v.values()) for v in self.counts.values())


def recompute_panel(
    symbols: Sequence[str],
    *,
    ema_specs: Iterable[EMAStack] = (ind_svc.DEFAULT_EMA_STACK,),
    rsi_specs: Iterable[RSISpec] = (ind_svc.DEFAULT_RSI,),
    mode: str = "auto",
) -> PanelReport:
    """Batched replacement for looping ``recompute_for_symbol`` across symbols.

    Loads the close panel once, computes every configured indicator
    stack across all symbols in parallel, then writes results.  Same
    on-disk doc shape and ``mode`` semantics as the scalar path.
    """
    # Import here to avoid a module-level cycle (indicators.py → panel.py via tests).
    from market_analysis.services import indicators_vec as vec

    panel = load_close_panel(symbols)
    report = PanelReport(symbols=len(panel.symbols), bars=int(panel.shape[0]), counts={})
    if panel.shape[0] == 0 or panel.shape[1] == 0:
        log.warning("Empty panel for %d symbol(s); nothing to recompute.", len(symbols))
        return report

    for spec in ema_specs:
        s_arr = vec.ema_panel(panel.closes, spec.short)
        m_arr = vec.ema_panel(panel.closes, spec.middle)
        l_arr = vec.ema_panel(panel.closes, spec.long)
        params = {"short": spec.short, "middle": spec.middle, "long": spec.long}
        counts = write_indicator_panel(
            panel, "ema", spec.stack, params,
            {"short": s_arr, "middle": m_arr, "long": l_arr},
            mode=mode,
        )
        report.counts[f"ema/stack={spec.stack}"] = counts

    for spec in rsi_specs:
        v_arr = vec.rsi_panel(panel.closes, spec.period)
        params = {"period": spec.period}
        counts = write_indicator_panel(
            panel, "rsi", spec.stack, params, {"value": v_arr}, mode=mode,
        )
        report.counts[f"rsi/stack={spec.stack}"] = counts

    return report
