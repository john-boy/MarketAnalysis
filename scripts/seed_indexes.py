"""Seed the ``indexes`` collection with a common set of market indexes.

Idempotent.  Creates the collection and its unique ``symbol`` index if
missing, then upserts the seed records (``$setOnInsert`` only, so
user edits to existing rows are preserved).

Run once after ``migrate_from_prototype.py`` has landed the base
schema; safe to re-run at any time.

Usage::

    python -m scripts.seed_indexes
"""

from __future__ import annotations

import sys

from pymongo import ASCENDING

from market_analysis.data import mongo
from market_analysis.data.models import MarketIndex


SEED_INDEXES: list[MarketIndex] = [
    MarketIndex(
        symbol="SPX",
        name="S&P 500 Index",
        category="broad_market",
        fetch_symbol="SPX",
        description="500 large-cap US equities.  Fetched directly via AV INDEX_DATA.",
    ),
    MarketIndex(
        symbol="NDX",
        name="NASDAQ-100 Index",
        category="broad_market",
        fetch_symbol="NDX",
        description="100 largest non-financial NASDAQ companies.  AV INDEX_DATA.",
    ),
    MarketIndex(
        symbol="DJI",
        name="Dow Jones Industrial Average",
        category="broad_market",
        fetch_symbol="DJI",
        description="Price-weighted average of 30 US industrials.  AV INDEX_DATA.",
    ),
    MarketIndex(
        symbol="VIX",
        name="CBOE Volatility Index",
        category="volatility",
        fetch_symbol="VIX",
        description="30-day implied vol of SPX options.  AV INDEX_DATA.",
    ),
]


def _ensure_collection_and_index() -> None:
    """Create the ``indexes`` collection and unique index idempotently."""
    dbh = mongo.db()
    if mongo.INDEXES not in dbh.list_collection_names():
        dbh.create_collection(mongo.INDEXES)
    mongo.indexes().create_index(
        [("symbol", ASCENDING)], unique=True, name="symbol"
    )


def run() -> dict[str, list[str]]:
    _ensure_collection_and_index()
    coll = mongo.indexes()
    inserted: list[str] = []
    existed: list[str] = []
    migrated: list[str] = []
    for idx in SEED_INDEXES:
        result = coll.update_one(
            {"symbol": idx.symbol},
            {"$setOnInsert": idx.to_mongo()},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted.append(idx.symbol)
            continue
        existed.append(idx.symbol)
        # One-shot migration: pre-v1.0 seeds stored SPX/NDX/DJI in
        # proxy mode.  The new seed has them in direct mode (AV
        # INDEX_DATA).  Flip any stale row to match the current seed,
        # clearing the opposite field so the MarketIndex validator is
        # happy on next load.  Clears stale error state too.
        current = coll.find_one({"symbol": idx.symbol}, projection={
            "proxy_symbol": 1, "fetch_symbol": 1, "_id": 0,
        })
        if not current:
            continue
        want_fetch = idx.fetch_symbol
        want_proxy = idx.proxy_symbol
        has_fetch = current.get("fetch_symbol")
        has_proxy = current.get("proxy_symbol")
        if (want_fetch and has_fetch == want_fetch and not has_proxy) or (
            want_proxy and has_proxy == want_proxy and not has_fetch
        ):
            continue
        update: dict = {
            "$set": {
                "fetch_symbol": want_fetch,
                "proxy_symbol": want_proxy,
                "description": idx.description,
                "last_error": None,
            },
        }
        coll.update_one({"symbol": idx.symbol}, update)
        migrated.append(idx.symbol)
    return {"inserted": inserted, "existed": existed, "migrated": migrated}


def main() -> int:
    print("Seeding market indexes...")
    result = run()
    for name in result["inserted"]:
        print(f"  inserted   {name}")
    for name in result["existed"]:
        print(f"  existed    {name}")
    for name in result.get("migrated", []):
        print(f"  migrated   {name}  (proxy → direct INDEX_DATA)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
