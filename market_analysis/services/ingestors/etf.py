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
        if not sym:
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
