"""Company fundamentals ingestor.

Fetches Alpha Vantage's ``OVERVIEW`` payload for a single symbol,
normalizes the string-typed fields into real Python numbers / dates,
and upserts into the ``companies`` collection — both as convenience
top-level fields (sector, industry, exchange, …) and as a full
``fundamentals`` blob for downstream consumers who want every field
AV shipped.

AV returns every value as a string (``"45.6"``, ``"-"``, ``"None"``,
``"25.30%"``, etc.).  The parser here coerces the well-known scalars
while leaving unknown keys untouched so new AV fields survive a
release without a code change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from market_analysis.data import mongo
from market_analysis.data.models import utcnow
from market_analysis.sources.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageError,
)


log = logging.getLogger(__name__)


# -- Parsing --------------------------------------------------------------

#: AV keys whose values are plain decimal numbers (price, ratio, …).
#: We keep the list explicit so unknown keys pass through as strings,
#: which is safer than a blanket ``float()`` attempt.
_NUMERIC_KEYS: frozenset[str] = frozenset({
    "MarketCapitalization", "EBITDA", "PERatio", "PEGRatio", "BookValue",
    "DividendPerShare", "DividendYield", "EPS", "RevenuePerShareTTM",
    "ProfitMargin", "OperatingMarginTTM", "ReturnOnAssetsTTM",
    "ReturnOnEquityTTM", "RevenueTTM", "GrossProfitTTM", "DilutedEPSTTM",
    "QuarterlyEarningsGrowthYOY", "QuarterlyRevenueGrowthYOY",
    "AnalystTargetPrice", "AnalystRatingStrongBuy", "AnalystRatingBuy",
    "AnalystRatingHold", "AnalystRatingSell", "AnalystRatingStrongSell",
    "TrailingPE", "ForwardPE", "PriceToSalesRatioTTM", "PriceToBookRatio",
    "EVToRevenue", "EVToEBITDA", "Beta", "52WeekHigh", "52WeekLow",
    "50DayMovingAverage", "200DayMovingAverage", "SharesOutstanding",
    "PayoutRatio",
})

#: AV keys whose values are YYYY-MM-DD dates.
_DATE_KEYS: frozenset[str] = frozenset({
    "LatestQuarter", "DividendDate", "ExDividendDate",
})


def _parse_number(v: Any) -> float | None:
    """Coerce AV scalar strings to float, rejecting sentinels."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s.lower() in {"none", "-", "n/a", "na"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(v: Any) -> datetime | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"none", "-"}:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


#: Top-level `companies` keys we surface outside the ``fundamentals``
#: blob (the Company model declares them as real fields).
_TOP_LEVEL_MAP: dict[str, str] = {
    "Name": "name",
    "Sector": "sector",
    "Industry": "industry",
    "Exchange": "exchange",
    "Country": "country",
    "Currency": "currency",
    "CIK": "cik",
    "FiscalYearEnd": "fiscal_year_end",
    "Description": "description",
}


def _parse_overview(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split an AV OVERVIEW payload into (top-level, fundamentals) dicts.

    - Top-level dict goes into the ``companies`` doc's real fields
      (``sector``, ``industry``, …) for fast filtering.
    - Fundamentals dict holds every AV key, with numeric and date
      strings coerced to native types.  Unknown keys pass through.
    """
    top: dict[str, Any] = {}
    fund: dict[str, Any] = {}
    for k, v in payload.items():
        if k in _NUMERIC_KEYS:
            fund[k] = _parse_number(v)
        elif k in _DATE_KEYS:
            fund[k] = _parse_date(v)
        else:
            # Leave raw; includes strings like Sector, Name, Description.
            fund[k] = v if v not in ("None", "-", "") else None
        if k in _TOP_LEVEL_MAP:
            top[_TOP_LEVEL_MAP[k]] = fund[k]
    return top, fund


def _looks_empty(payload: dict[str, Any]) -> bool:
    """True if AV returned nothing useful (unsupported symbol / throttle)."""
    if not payload:
        return True
    # AV returns {"Information": "..."} for throttle / unsupported.
    if set(payload.keys()) <= {"Information", "Note", "Error Message"}:
        return True
    # Even when keys are present, OVERVIEW sometimes echoes "None" for all.
    return not any(payload.get(k) for k in ("Symbol", "Name"))


# -- Public API -----------------------------------------------------------


@dataclass
class FundamentalsReport:
    symbol: str
    updated: bool = False
    fields_seen: int = 0
    error: str | None = None
    skipped_reason: str | None = None


def ingest_fundamentals(
    symbol: str,
    *,
    client: AlphaVantageClient | None = None,
) -> FundamentalsReport:
    """Fetch + upsert fundamentals for ``symbol``.

    Idempotent: repeat calls overwrite the ``fundamentals`` blob and
    refresh the convenience top-level fields.  Never deletes existing
    fields that AV didn't return (uses ``$set``).
    """
    av = client or AlphaVantageClient()
    sym = symbol.upper()
    try:
        payload = av.overview(sym)
    except AlphaVantageError as e:
        return FundamentalsReport(symbol=sym, error=f"AV error: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("Fundamentals fetch failed for %s", sym)
        return FundamentalsReport(symbol=sym, error=f"unexpected: {e}")

    if _looks_empty(payload):
        return FundamentalsReport(
            symbol=sym,
            skipped_reason="AV returned no fundamentals (unsupported or throttled)",
        )

    top, fund = _parse_overview(payload)

    sets: dict[str, Any] = {
        "symbol": sym,
        "fundamentals": fund,
        "last_updated": utcnow(),
    }
    # Merge top-level scalars — skip falsy so we don't clobber values
    # that came from the ETF ingestor (e.g. ``name``) when AV returns a
    # blank.
    for k, v in top.items():
        if v is not None and v != "":
            sets[k] = v

    mongo.companies().update_one(
        {"symbol": sym},
        {"$set": sets},
        upsert=True,
    )
    return FundamentalsReport(symbol=sym, updated=True, fields_seen=len(fund))
