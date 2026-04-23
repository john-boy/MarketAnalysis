"""Seed the ``accounts`` collection with the three Schwab accounts.

Idempotent: upserts on ``nickname`` (the unique key).  Existing records
are not overwritten.

Usage::

    python -m scripts.seed_accounts
"""

from __future__ import annotations

import sys

from market_analysis.data import mongo
from market_analysis.data.models import Account


SCHWAB_ACCOUNTS: list[Account] = [
    Account(
        account_id="schwab-ira",            # placeholder; replaced after first API sync
        broker="schwab",
        nickname="Schwab IRA",
        access_mode="api",
        tax_type="ira",
        notes="Trader API; equities / options / mutual funds.",
    ),
    Account(
        account_id="schwab-joint",
        broker="schwab",
        nickname="Schwab Joint",
        access_mode="api",
        tax_type="joint",
        notes="Trader API; equities / options / mutual funds.",
    ),
    Account(
        account_id="schwab-futures",
        broker="schwab",
        nickname="Schwab Futures",
        access_mode="api",
        tax_type="futures",
        notes=(
            "Tentative: verify that the Schwab Trader API surfaces futures "
            "positions via /accounts and /accounts/{id}/positions after the "
            "first OAuth token exchange (see docs/plan.md Phase 4)."
        ),
    ),
]


def run() -> dict[str, list[str]]:
    coll = mongo.accounts()
    inserted: list[str] = []
    existed: list[str] = []
    for acct in SCHWAB_ACCOUNTS:
        result = coll.update_one(
            {"nickname": acct.nickname},
            {"$setOnInsert": acct.to_mongo()},
            upsert=True,
        )
        if result.upserted_id is not None:
            inserted.append(acct.nickname)
        else:
            existed.append(acct.nickname)
    return {"inserted": inserted, "existed": existed}


def main() -> int:
    print("Seeding Schwab accounts...")
    result = run()
    for name in result["inserted"]:
        print(f"  inserted   {name}")
    for name in result["existed"]:
        print(f"  existed    {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
