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
    # New: reconciliation results.
    dropped_symbols: list[str] = field(default_factory=list)
    memberships_pulled: int = 0


# Fields the user can override via the Admin UI. On a refresh we preserve
# whatever's already stored unless the stored value is missing/empty;
# AV only fills these in on first ingest.
_USER_OVERRIDE_FIELDS = ("name", "description")


def ingest_etf(
    symbol: str,
    *,
    client: AlphaVantageClient | None = None,
) -> ETFIngestReport:
    """Fetch ``ETF_PROFILE`` for ``symbol``; upsert ``etf`` + minimal companies.

    Reconciles the ETF's holdings against any existing doc: holdings that
    dropped out get pulled from each company's ``etf_memberships``. Companies
    are never deleted here — orphaned holdings (empty ``etf_memberships``)
    are surfaced to the daily updater so their price history keeps flowing
    until a user explicitly deletes them from the Company UI.
    """
    av = client or AlphaVantageClient()
    payload = av.etf_profile(symbol)

    model = _etf_from_payload(symbol, payload)
    doc = model.to_mongo()

    # Read the existing doc (if any) so we can (a) reconcile holdings and
    # (b) preserve user-supplied overrides.
    existing = mongo.etf().find_one({"symbol": symbol}) or {}
    prev_holding_symbols = {
        h.get("symbol") for h in (existing.get("holdings") or [])
        if h.get("symbol")
    }

    # Preserve user overrides: if the stored doc already has a non-empty
    # value for an override field, don't let AV clobber it.
    for fld in _USER_OVERRIDE_FIELDS:
        stored = existing.get(fld)
        if stored not in (None, ""):
            # Drop from $set so the existing value survives.
            doc.pop(fld, None)
        elif doc.get(fld) is None:
            # AV didn't provide anything either — don't write a null.
            doc.pop(fld, None)

    # Upsert ETF by symbol.
    mongo.etf().update_one(
        {"symbol": symbol},
        {"$set": doc},
        upsert=True,
    )

    new_holding_symbols = {h.symbol for h in model.holdings}
    dropped = sorted(prev_holding_symbols - new_holding_symbols)

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

    # Reconcile: for every holding that dropped out, pull this ETF from
    # the company's ``etf_memberships``. The company row itself is left in
    # place — even if etf_memberships is now empty, the user may still want
    # to track price history. Deletion is a manual op from the Company UI.
    memberships_pulled = 0
    if dropped:
        res = comp_coll.update_many(
            {"symbol": {"$in": dropped}, "etf_memberships": symbol},
            {"$pull": {"etf_memberships": symbol}},
        )
        memberships_pulled = int(res.modified_count)

    return ETFIngestReport(
        symbol=symbol,
        holdings_count=len(model.holdings),
        companies_upserted=companies_upserted,
        holding_symbols=[h.symbol for h in model.holdings],
        dropped_symbols=dropped,
        memberships_pulled=memberships_pulled,
    )


def set_etf_name(symbol: str, name: str | None) -> bool:
    """Set or clear the display name on an ETF row.

    Returns True if the row exists and was updated.
    """
    return _set_etf_text_field(symbol, "name", name)


def set_etf_description(symbol: str, description: str | None) -> bool:
    """Set or clear the description on an ETF row.

    Returns True if the row exists and was updated.
    """
    return _set_etf_text_field(symbol, "description", description)


def _set_etf_text_field(symbol: str, field_name: str, value: str | None) -> bool:
    sym = symbol.upper()
    value = (value or "").strip() or None
    update: dict = (
        {"$set": {field_name: value}} if value else {"$unset": {field_name: ""}}
    )
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


def delete_company(symbol: str) -> dict[str, int]:
    """Hard-delete a company plus its price history and indicators.

    Intended for use from the Company UI when the user wants to remove an
    orphaned (no ETF membership) symbol. Also defensively pulls the symbol
    from every ``etf.holdings`` and ``companies.etf_memberships`` entry so
    nothing in the system points at the deleted row.

    Returns a dict with per-collection delete counts.
    """
    sym = symbol.upper()
    out = {"companies": 0, "price_history": 0, "indicators": 0}

    # Defensive: scrub any remaining references.
    mongo.companies().update_many(
        {"etf_memberships": sym},
        {"$pull": {"etf_memberships": sym}},
    )
    mongo.etf().update_many(
        {"holdings.symbol": sym},
        {"$pull": {"holdings": {"symbol": sym}}},
    )

    out["companies"]     = mongo.companies().delete_one({"symbol": sym}).deleted_count
    out["price_history"] = mongo.price_history().delete_many({"symbol": sym}).deleted_count
    out["indicators"]    = mongo.indicators().delete_many({"symbol": sym}).deleted_count
    return out


def list_orphaned_companies() -> list[str]:
    """Return symbols of companies whose ``etf_memberships`` is empty or missing.

    Surfaced from the daily updater so orphaned holdings still get price /
    indicator refreshes until the user deletes them explicitly.
    """
    cur = mongo.companies().find(
        {
            "$or": [
                {"etf_memberships": {"$exists": False}},
                {"etf_memberships": {"$size": 0}},
            ]
        },
        {"_id": 0, "symbol": 1},
    )
    return sorted(d["symbol"] for d in cur if d.get("symbol"))
