"""ETF ingestor.

Given an ETF symbol, fetches the ETF profile (Alpha Vantage
``ETF_PROFILE``) and upserts:

- one doc in ``etf`` (holdings + sector weights),
- one minimal doc per holding in ``companies`` (``symbol`` + ``name``
  + the owning ETF pushed onto ``etf_memberships``).

Returns the list of holding symbols so a caller can drive downstream
price ingestion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from market_analysis.data import mongo
from market_analysis.data.models import ETF, Holding, utcnow
from market_analysis.services.ingestors._common import is_tradeable_symbol
from market_analysis.sources.alpha_vantage import AlphaVantageClient


log = logging.getLogger(__name__)


# -- AV payload normalization --------------------------------------------


def _to_float(v: Any) -> float | None:
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_holdings(raw: Any) -> list[Holding]:
    if not isinstance(raw, list):
        return []
    out: list[Holding] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        sym = item.get("symbol") or item.get("Symbol")
        if not is_tradeable_symbol(sym):
            # Skip cash / N.A. / placeholder "holdings" that AV emits for
            # unallocated fund buckets — they have no price series.
            continue
        out.append(Holding(
            symbol=sym,
            name=item.get("description") or item.get("name"),
            weight=_to_float(item.get("weight")),
        ))
    return out


def _parse_sector_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, list):
        return {}
    out: dict[str, float] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        sector = item.get("sector") or item.get("Sector")
        w = _to_float(item.get("weight"))
        if sector and w is not None:
            out[sector] = w
    return out


def _etf_from_payload(symbol: str, payload: dict[str, Any]) -> ETF:
    return ETF(
        symbol=symbol,
        name=payload.get("name"),
        provider=payload.get("issuer"),
        description=payload.get("description"),
        expense_ratio=_to_float(payload.get("net_expense_ratio")),
        asset_class=payload.get("asset_class"),
        holdings=_parse_holdings(payload.get("holdings")),
        sector_weights=_parse_sector_weights(payload.get("sectors")),
        last_updated=utcnow(),
    )


# -- Public API -----------------------------------------------------------


@dataclass
class ETFIngestReport:
    symbol: str
    holdings_count: int = 0
    companies_upserted: int = 0
    holding_symbols: list[str] = field(default_factory=list)


def ingest_etf(
    symbol: str,
    *,
    client: AlphaVantageClient | None = None,
) -> ETFIngestReport:
    """Fetch ``ETF_PROFILE`` for ``symbol``; upsert ``etf`` + minimal companies."""
    av = client or AlphaVantageClient()
    payload = av.etf_profile(symbol)

    model = _etf_from_payload(symbol, payload)
    doc = model.to_mongo()

    # Don't clobber a user-supplied display name with AV's ``None``.
    # If AV's ETF_PROFILE has no ``name`` for this ticker, leave whatever
    # name is already stored in place (the user may have set it from the
    # Admin UI).
    if doc.get("name") is None:
        doc.pop("name", None)

    # Upsert ETF by symbol.
    mongo.etf().update_one(
        {"symbol": symbol},
        {"$set": doc},
        upsert=True,
    )

    # Upsert minimal companies + track ETF membership.
    comp_coll = mongo.companies()
    companies_upserted = 0
    for h in model.holdings:
        comp_coll.update_one(
            {"symbol": h.symbol},
            {
                "$set": {"symbol": h.symbol, **({"name": h.name} if h.name else {})},
                "$addToSet": {"etf_memberships": symbol},
            },
            upsert=True,
        )
        companies_upserted += 1

    return ETFIngestReport(
        symbol=symbol,
        holdings_count=len(model.holdings),
        companies_upserted=companies_upserted,
        holding_symbols=[h.symbol for h in model.holdings],
    )


def set_etf_name(symbol: str, name: str | None) -> bool:
    """Set or clear the display name on an ETF row.

    Returns True if the row exists and was updated.
    """
    sym = symbol.upper()
    name = (name or "").strip() or None
    update: dict = {"$set": {"name": name}} if name else {"$unset": {"name": ""}}
    return mongo.etf().update_one({"symbol": sym}, update).matched_count > 0


def list_tracked_etfs() -> list[str]:
    """Return sorted symbols of every ETF currently in the ``etf`` collection."""
    return sorted(mongo.etf().distinct("symbol"))


def remove_etf(symbol: str) -> bool:
    """Remove an ETF profile from the ``etf`` collection.

    Does **not** delete the holdings' price history — that data is
    shared across multiple ETFs and is preserved unless the user
    explicitly cleans it up.  Company membership lists are trimmed so
    the removed ETF no longer appears in ``etf_memberships``.

    Returns True iff a row was deleted.
    """
    sym = symbol.upper()
    mongo.companies().update_many(
        {"etf_memberships": sym},
        {"$pull": {"etf_memberships": sym}},
    )
    res = mongo.etf().delete_one({"symbol": sym})
    return res.deleted_count > 0
