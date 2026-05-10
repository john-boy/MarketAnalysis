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
    from market_analysis.config import get_settings
    s = get_settings()

    if not mongo.ping(timeout_ms=500):
        return DBHealth(
            reachable=False, schema_version=None, counts={},
            uri=s.mongo.uri, database=s.mongo.database,
        )

    schema_doc = mongo.schema_version().find_one({"_id": "schema_version"})
    version = schema_doc.get("version") if schema_doc else None

    counts: dict[str, int] = {}
    for name in (*mongo.WYCKOFF_COLLECTIONS, mongo.SCHEMA_VERSION):
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
    """Return distinct symbols present in ``price_history``, sorted."""
    syms = mongo.price_history().distinct("metadata.symbol")
    return sorted(s for s in syms if s)


@dataclass
class QuoteRow:
    date: datetime
    close: float
    adjusted_close: float | None


def load_quotes(symbol: str, *, limit: int | None = None) -> list[QuoteRow]:
    """Return ascending quote rows for ``symbol`` (date, close, adjusted_close)."""
    cur = mongo.price_history().find(
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


@dataclass
class FundamentalsView:
    """Display-ready fundamentals for one symbol.

    Sections are lists of ``(label, formatted_value)`` tuples already
    in the order the UI should render them.  Missing values are
    represented by ``"—"`` so rows still appear in-place — useful for
    spotting what the data provider has vs. hasn't populated.

    ``has_data`` is False when the underlying ``companies`` doc either
    doesn't exist or has no fundamentals yet (no OVERVIEW ingest has
    run for this symbol).
    """
    symbol: str
    has_data: bool
    name: str | None
    description: str | None
    identity: list[tuple[str, str]]
    valuation: list[tuple[str, str]]
    profitability: list[tuple[str, str]]
    dividend: list[tuple[str, str]]
    trading: list[tuple[str, str]]
    analyst: list[tuple[str, str]]
    etf_memberships: list[str]


def load_fundamentals_view(symbol: str) -> FundamentalsView:
    """Build a :class:`FundamentalsView` for ``symbol``.

    Uses whatever data is present in ``companies`` — works even when
    ``fundamentals`` is empty (sections just show ``—``).
    """
    doc = load_company(symbol) or {}
    fund: dict[str, Any] = doc.get("fundamentals") or {}
    has_data = bool(fund)

    def g(key: str) -> Any:
        return fund.get(key)

    identity = [
        ("Sector", _s(doc.get("sector"))),
        ("Industry", _s(doc.get("industry"))),
        ("Exchange", _s(doc.get("exchange"))),
        ("Country", _s(doc.get("country"))),
        ("Currency", _s(doc.get("currency"))),
        ("CIK", _s(doc.get("cik"))),
        ("Fiscal year end", _s(doc.get("fiscal_year_end"))),
        ("Latest quarter", _date(g("LatestQuarter"))),
    ]

    valuation = [
        ("Market cap", _money(g("MarketCapitalization"))),
        ("Enterprise value / rev.", _ratio(g("EVToRevenue"))),
        ("Enterprise value / EBITDA", _ratio(g("EVToEBITDA"))),
        ("P/E (trailing)", _ratio(g("TrailingPE") or g("PERatio"))),
        ("P/E (forward)", _ratio(g("ForwardPE"))),
        ("PEG", _ratio(g("PEGRatio"))),
        ("P/S", _ratio(g("PriceToSalesRatioTTM"))),
        ("P/B", _ratio(g("PriceToBookRatio"))),
        ("Book value / sh.", _money(g("BookValue"))),
    ]

    profitability = [
        ("Revenue TTM", _money(g("RevenueTTM"))),
        ("Gross profit TTM", _money(g("GrossProfitTTM"))),
        ("EBITDA", _money(g("EBITDA"))),
        ("EPS (diluted TTM)", _money(g("DilutedEPSTTM") or g("EPS"))),
        ("Profit margin", _pct(g("ProfitMargin"))),
        ("Operating margin TTM", _pct(g("OperatingMarginTTM"))),
        ("Return on assets TTM", _pct(g("ReturnOnAssetsTTM"))),
        ("Return on equity TTM", _pct(g("ReturnOnEquityTTM"))),
        ("Quarterly earnings growth YoY", _pct(g("QuarterlyEarningsGrowthYOY"))),
        ("Quarterly revenue growth YoY", _pct(g("QuarterlyRevenueGrowthYOY"))),
    ]

    dividend = [
        ("Dividend / sh.", _money(g("DividendPerShare"))),
        ("Dividend yield", _pct(g("DividendYield"))),
        ("Payout ratio", _pct(g("PayoutRatio"))),
        ("Ex-dividend date", _date(g("ExDividendDate"))),
        ("Dividend date", _date(g("DividendDate"))),
    ]

    trading = [
        ("Beta", _ratio(g("Beta"))),
        ("52-week high", _money(g("52WeekHigh"))),
        ("52-week low", _money(g("52WeekLow"))),
        ("50-day MA", _money(g("50DayMovingAverage"))),
        ("200-day MA", _money(g("200DayMovingAverage"))),
        ("Shares outstanding", _int(g("SharesOutstanding"))),
    ]

    analyst = [
        ("Target price", _money(g("AnalystTargetPrice"))),
        ("Strong buy", _int(g("AnalystRatingStrongBuy"))),
        ("Buy", _int(g("AnalystRatingBuy"))),
        ("Hold", _int(g("AnalystRatingHold"))),
        ("Sell", _int(g("AnalystRatingSell"))),
        ("Strong sell", _int(g("AnalystRatingStrongSell"))),
    ]

    return FundamentalsView(
        symbol=symbol,
        has_data=has_data,
        name=doc.get("name") or fund.get("Name"),
        description=doc.get("description") or fund.get("Description"),
        identity=identity,
        valuation=valuation,
        profitability=profitability,
        dividend=dividend,
        trading=trading,
        analyst=analyst,
        etf_memberships=list(doc.get("etf_memberships") or []),
    )


def load_etf(symbol: str) -> dict[str, Any] | None:
    doc = mongo.etf().find_one({"symbol": symbol})
    if doc:
        doc.pop("_id", None)
    return doc


# -- Indexes --------------------------------------------------------------


def list_indexes() -> list[dict[str, Any]]:
    """Return tracked indexes as plain dicts, sorted by symbol.

    Includes resolved ``mode`` and ``price_source_symbol`` so the UI
    doesn't need to reimplement the MarketIndex property logic.
    """
    from market_analysis.data.models import MarketIndex
    out: list[dict[str, Any]] = []
    for doc in mongo.indexes().find({}).sort("symbol", 1):
        rec = MarketIndex.model_validate(doc)
        out.append({
            "symbol": rec.symbol,
            "name": rec.name,
            "category": rec.category,
            "mode": rec.mode,
            "proxy_symbol": rec.proxy_symbol,
            "fetch_symbol": rec.fetch_symbol,
            "price_source_symbol": rec.price_source_symbol,
            "description": rec.description,
            "last_ingested": rec.last_ingested,
            "last_error": rec.last_error,
        })
    return out


# -- Watchlist ------------------------------------------------------------


def list_watchlist() -> list[dict[str, Any]]:
    """Return every watchlist entry as a plain dict, sorted by symbol.

    Each row is enriched with ``last_quote_date`` (the most recent bar
    in ``price_history`` for that symbol, or ``None``) so the UI can
    surface coverage at a glance without a second round-trip per row.
    """
    from market_analysis.data.models import WatchlistEntry

    rows: list[dict[str, Any]] = []
    for doc in mongo.watchlist().find({}).sort("symbol", 1):
        entry = WatchlistEntry.model_validate(doc)
        last = mongo.price_history().find_one(
            {"metadata.symbol": entry.symbol},
            sort=[("date", -1)],
            projection={"date": 1, "_id": 0},
        )
        rows.append({
            "symbol": entry.symbol,
            "themes": entry.themes,
            "ingest_tags": entry.ingest_tags,
            "notes": entry.notes,
            "source_of_origin": entry.source_of_origin,
            "added_at": entry.added_at,
            "last_updated": entry.last_updated,
            "last_quote_date": last["date"] if last else None,
        })
    return rows


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


# -- Fundamentals display formatters --------------------------------------
#
# AV strings (e.g. "2250000000") are already parsed to floats by the
# fundamentals ingestor.  These helpers render them for humans.

_EMPTY = "—"


def _s(v: Any) -> str:
    if v is None or v == "":
        return _EMPTY
    return str(v)


def _money(v: Any) -> str:
    """Scale large money values with K/M/B/T suffixes."""
    x = _num(v)
    if x is None:
        return _EMPTY
    absx = abs(x)
    if absx >= 1e12:
        return f"${x / 1e12:,.2f}T"
    if absx >= 1e9:
        return f"${x / 1e9:,.2f}B"
    if absx >= 1e6:
        return f"${x / 1e6:,.2f}M"
    if absx >= 1e3:
        return f"${x / 1e3:,.2f}K"
    return f"${x:,.2f}"


def _ratio(v: Any) -> str:
    x = _num(v)
    return _EMPTY if x is None else f"{x:,.2f}"


def _pct(v: Any) -> str:
    """AV reports margins as decimals (0.23 = 23%)."""
    x = _num(v)
    return _EMPTY if x is None else f"{x * 100:+.2f}%"


def _int(v: Any) -> str:
    x = _num(v)
    return _EMPTY if x is None else f"{int(x):,}"


def _date(v: Any) -> str:
    if v is None:
        return _EMPTY
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    return str(v)
