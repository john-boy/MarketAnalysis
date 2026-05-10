"""Technical indicator engine.

Phase 1 scope: EMA (three-period stack → ``short``/``middle``/``long``)
and RSI (single ``value``).  Docs are written into the ``indicators``
time-series collection in the shape carried forward from the
prototype::

    {
        "date":     <datetime>,
        "metadata": {"symbol": "AAPL",
                     "indicator": "ema",
                     "stack": 0,
                     "params": {"short": 12, "middle": 26, "long": 50}},
        "short": 170.1, "middle": 170.2, "long": 170.3
    }

Time-series collections don't support unique indexes, so we refresh
by ``delete_many`` on the metadata key then ``insert_many``.  For
daily cadence with small per-symbol volumes (~6 k docs / 25 years)
this is fast and deterministic.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from market_analysis.data import mongo


log = logging.getLogger(__name__)


# -- Stack specs ----------------------------------------------------------


@dataclass(frozen=True)
class EMAStack:
    """Three-period EMA (``short``/``middle``/``long``) with a stack id."""

    short: int = 12
    middle: int = 26
    long: int = 50
    stack: int = 0


@dataclass(frozen=True)
class RSISpec:
    """Wilder-smoothed RSI."""

    period: int = 14
    stack: int = 0


DEFAULT_EMA_STACK = EMAStack()
DEFAULT_RSI = RSISpec()


# -- Pure math ------------------------------------------------------------


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average.

    Standard smoothing (α = 2/(N+1)) seeded with the simple mean of the
    first ``period`` values.  Returns a list aligned with ``values``;
    positions before the seed are ``None``.
    """
    if period < 1:
        raise ValueError("period must be >= 1")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder-smoothed RSI.  Returns values in 0..100, ``None`` before seed."""
    if period < 2:
        raise ValueError("period must be >= 2")
    n = len(values)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_from(avg_gain, avg_loss)
    for i in range(period + 1, n):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# -- Engine ---------------------------------------------------------------


@dataclass
class IndicatorReport:
    symbol: str
    counts: dict[str, int] = field(default_factory=dict)
    quotes_read: int = 0


def _load_quote_series(symbol: str) -> tuple[list[datetime], list[float]]:
    """Return aligned ``(dates, adj_close)`` for ``symbol``, ascending.

    Reads ``price_history``: prefers the WyckoffDB-computed ``adj_close``
    (split + dividend adjusted), falling back to ``adjusted_close`` and
    finally raw ``close`` for safety. Indicators must use adjusted prices
    so EMAs / RSIs are continuous across split boundaries.
    """
    coll = mongo.price_history()
    cur = coll.find(
        {"metadata.symbol": symbol},
        {"date": 1, "adj_close": 1, "adjusted_close": 1, "close": 1, "_id": 0},
    ).sort("date", 1)

    dates: list[datetime] = []
    closes: list[float] = []
    for doc in cur:
        price = doc.get("adj_close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            price = doc.get("adjusted_close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            price = doc.get("close")
        if price is None or (isinstance(price, float) and math.isnan(price)):
            continue
        dates.append(doc["date"])
        closes.append(float(price))
    return dates, closes


def _refresh(
    symbol: str,
    indicator: str,
    stack: int,
    docs: list[dict[str, Any]],
) -> int:
    """Delete existing ``(symbol, indicator, stack)`` docs and insert new."""
    coll = mongo.indicators()
    coll.delete_many({
        "metadata.symbol": symbol,
        "metadata.indicator": indicator,
        "metadata.stack": stack,
    })
    if not docs:
        return 0
    coll.insert_many(docs, ordered=False)
    return len(docs)


def _last_indicator_date(symbol: str, indicator: str, stack: int) -> datetime | None:
    doc = mongo.indicators().find_one(
        {
            "metadata.symbol": symbol,
            "metadata.indicator": indicator,
            "metadata.stack": stack,
        },
        sort=[("date", -1)],
        projection={"date": 1, "_id": 0},
    )
    return doc["date"] if doc else None


def _append(
    symbol: str,
    indicator: str,
    stack: int,
    docs: list[dict[str, Any]],
    last_date: datetime,
) -> int:
    """Insert only docs newer than ``last_date``; no delete."""
    new = [d for d in docs if d["date"] > last_date]
    if not new:
        return 0
    mongo.indicators().insert_many(new, ordered=False)
    return len(new)


def compute_ema_docs(
    symbol: str,
    dates: Sequence[datetime],
    closes: Sequence[float],
    spec: EMAStack = DEFAULT_EMA_STACK,
) -> list[dict[str, Any]]:
    """Return time-series docs for an EMA stack."""
    s_vals = ema(closes, spec.short)
    m_vals = ema(closes, spec.middle)
    l_vals = ema(closes, spec.long)
    params = {"short": spec.short, "middle": spec.middle, "long": spec.long}
    docs: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        s, m, l = s_vals[i], m_vals[i], l_vals[i]
        if s is None and m is None and l is None:
            continue
        docs.append({
            "date": d,
            "metadata": {
                "symbol": symbol,
                "indicator": "ema",
                "stack": spec.stack,
                "params": params,
            },
            "short": s,
            "middle": m,
            "long": l,
        })
    return docs


def compute_rsi_docs(
    symbol: str,
    dates: Sequence[datetime],
    closes: Sequence[float],
    spec: RSISpec = DEFAULT_RSI,
) -> list[dict[str, Any]]:
    vals = rsi(closes, spec.period)
    params = {"period": spec.period}
    docs: list[dict[str, Any]] = []
    for i, d in enumerate(dates):
        v = vals[i]
        if v is None:
            continue
        docs.append({
            "date": d,
            "metadata": {
                "symbol": symbol,
                "indicator": "rsi",
                "stack": spec.stack,
                "params": params,
            },
            "value": v,
        })
    return docs


def recompute_for_symbol(
    symbol: str,
    *,
    ema_specs: Iterable[EMAStack] = (DEFAULT_EMA_STACK,),
    rsi_specs: Iterable[RSISpec] = (DEFAULT_RSI,),
    mode: str = "auto",
) -> IndicatorReport:
    """Compute configured indicators for ``symbol`` from stored quotes.

    ``mode``:
    - ``"auto"``  — per (indicator, stack) pair: if any docs already
      exist, append only rows newer than the last stored date; if none
      exist, do a full refresh.  Math is always recomputed over the
      full series (reads are cheap) so RSI state is exact.
    - ``"full"``  — full ``delete`` + ``insert`` for every pair
      (testing / repair).
    """
    if mode not in ("auto", "full"):
        raise ValueError(f"Unknown indicator mode: {mode!r}")

    dates, closes = _load_quote_series(symbol)
    report = IndicatorReport(symbol=symbol, quotes_read=len(dates))
    if not dates:
        log.warning("No quotes for %s; nothing to recompute.", symbol)
        return report

    for spec in ema_specs:
        docs = compute_ema_docs(symbol, dates, closes, spec)
        n = _write(symbol, "ema", spec.stack, docs, mode)
        report.counts[f"ema/stack={spec.stack}"] = n

    for spec in rsi_specs:
        docs = compute_rsi_docs(symbol, dates, closes, spec)
        n = _write(symbol, "rsi", spec.stack, docs, mode)
        report.counts[f"rsi/stack={spec.stack}"] = n

    return report


def _write(
    symbol: str, indicator: str, stack: int,
    docs: list[dict[str, Any]], mode: str,
) -> int:
    """Dispatch to full-refresh or append-only based on ``mode``."""
    if mode == "full":
        return _refresh(symbol, indicator, stack, docs)
    # auto
    last = _last_indicator_date(symbol, indicator, stack)
    if last is None:
        return _refresh(symbol, indicator, stack, docs)
    return _append(symbol, indicator, stack, docs, last)
