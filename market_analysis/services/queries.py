"""Read-only query helpers for the UI layer.

Tabs obtain data exclusively through this module (and a handful of
siblings in ``services/``); they must not import
``market_analysis.data`` or ``market_analysis.sources`` directly.

Functions here return plain Python types (``list`` / ``dict`` /
scalars) so the UI layer never handles raw Mongo cursors or driver
objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from market_analysis.data import mongo


# -- DB health ------------------------------------------------------------


@dataclass
class DBHealth:
    reachable: bool
    schema_version: int | None
    counts: dict[str, int]
    uri: str
    database: str


def db_health() -> DBHealth:
    """Return connectivity + per-collection document counts."""
    from market_analysis.services.config import get_settings
    s = get_settings()

    if not mongo.ping(timeout_ms=500):
        return DBHealth(
            reachable=False, schema_version=None, counts={},
            uri=s.mongo.uri, database=s.mongo.database,
        )

    schema_doc = mongo.schema_version().find_one({"_id": "schema_version"})
    version = schema_doc.get("version") if schema_doc else None

    counts: dict[str, int] = {}
    for name in mongo.ALL_COLLECTIONS:
        try:
            counts[name] = mongo.db()[name].estimated_document_count()
        except Exception:
            counts[name] = -1

    return DBHealth(
        reachable=True,
        schema_version=version,
        counts=counts,
        uri=s.mongo.uri,
        database=s.mongo.database,
    )


# -- Symbol / quote queries -----------------------------------------------


def list_symbols_with_quotes() -> list[str]:
    """Return distinct symbols present in ``daily_quotes``, sorted."""
    syms = mongo.daily_quotes().distinct("metadata.symbol")
    return sorted(s for s in syms if s)


@dataclass
class QuoteRow:
    date: datetime
    close: float
    adjusted_close: float | None


def load_quotes(symbol: str, *, limit: int | None = None) -> list[QuoteRow]:
    """Return ascending quote rows for ``symbol`` (date, close, adjusted_close)."""
    cur = mongo.daily_quotes().find(
        {"metadata.symbol": symbol},
        {"date": 1, "close": 1, "adjusted_close": 1, "_id": 0},
    ).sort("date", 1)
    if limit is not None:
        cur = cur.limit(limit)

    out: list[QuoteRow] = []
    for doc in cur:
        close = _num(doc.get("close"))
        if close is None:
            continue
        out.append(QuoteRow(
            date=doc["date"],
            close=close,
            adjusted_close=_num(doc.get("adjusted_close")),
        ))
    return out


# -- Indicator queries ----------------------------------------------------


@dataclass
class EMARow:
    date: datetime
    short: float | None
    middle: float | None
    long: float | None


@dataclass
class RSIRow:
    date: datetime
    value: float


def load_rsi(symbol: str, *, stack: int = 0) -> list[RSIRow]:
    """Return ascending RSI rows for ``symbol`` / ``stack``."""
    cur = mongo.indicators().find(
        {
            "metadata.symbol": symbol,
            "metadata.indicator": "rsi",
            "metadata.stack": stack,
        },
        {"date": 1, "value": 1, "_id": 0},
    ).sort("date", 1)
    out: list[RSIRow] = []
    for doc in cur:
        v = _num(doc.get("value"))
        if v is None:
            continue
        out.append(RSIRow(date=doc["date"], value=v))
    return out


def load_ema_stack(symbol: str, *, stack: int = 0) -> list[EMARow]:
    """Return ascending EMA stack rows for ``symbol`` / ``stack``."""
    cur = mongo.indicators().find(
        {
            "metadata.symbol": symbol,
            "metadata.indicator": "ema",
            "metadata.stack": stack,
        },
        {"date": 1, "short": 1, "middle": 1, "long": 1, "_id": 0},
    ).sort("date", 1)
    return [
        EMARow(
            date=doc["date"],
            short=_num(doc.get("short")),
            middle=_num(doc.get("middle")),
            long=_num(doc.get("long")),
        )
        for doc in cur
    ]


# -- Company / ETF --------------------------------------------------------


def load_company(symbol: str) -> dict[str, Any] | None:
    doc = mongo.companies().find_one({"symbol": symbol})
    if doc:
        doc.pop("_id", None)
    return doc


def load_etf(symbol: str) -> dict[str, Any] | None:
    doc = mongo.etf().find_one({"symbol": symbol})
    if doc:
        doc.pop("_id", None)
    return doc


# -- Helpers --------------------------------------------------------------


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v):
        return None
    return v
