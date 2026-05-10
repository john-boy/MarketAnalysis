"""Final cutover: drop the legacy MarketAnalysis database.

PR 4 of the WyckoffDB transition (see WYCKOFF_TRANSITION_CHECKLIST.md
Phase 7). Run this *after* the Phase 6 verification checklist passes.

Steps:
  1. Print pre-cutover sanity checks (target counts, asset_type spread).
  2. Confirm with the operator (or honor --yes for non-interactive).
  3. ``client.drop_database("MarketAnalysis")``.
  4. Verify MarketAnalysis no longer appears in ``listDatabaseNames``.

The matching code-side cleanup (deleting the wyckoff_* transition
scripts + the ``market_analysis_db()`` helper) is done in the same
PR commit, not by this script — so a single git revert can roll back
both halves if needed.

Usage::

    python -m scripts.wyckoff_cutover                # interactive
    python -m scripts.wyckoff_cutover --yes          # non-interactive
    python -m scripts.wyckoff_cutover --dry-run      # report only
"""

from __future__ import annotations

import argparse
import sys

from market_analysis.data import mongo


LEGACY_DB_NAME = "MarketAnalysis"


def _preflight() -> bool:
    """Print pre-cutover sanity. Returns True iff target looks healthy."""
    dst = mongo.db()
    print(f"Target DB: {dst.name}")
    print()
    print("Per-collection counts:")
    ok = True
    expected_min = {
        mongo.PRICE_HISTORY: 3_000_000,   # was 3,191,125 at migration
        mongo.COMPANIES: 500,             # was 517
        mongo.ETF: 10,                    # was 13
        mongo.WATCHLIST: 1,               # was 2
        mongo.INDEXES: 4,
        mongo.INDICATORS: 1,              # >= some indicator data
    }
    for name in mongo.WYCKOFF_COLLECTIONS:
        n = dst[name].estimated_document_count()
        floor = expected_min.get(name)
        flag = ""
        if floor is not None and n < floor:
            ok = False
            flag = f"  *** BELOW expected min {floor:,} ***"
        print(f"  {name:<22} {n:>12,}{flag}")

    print()
    print("price_history asset_type spread:")
    pipeline = [{"$group": {"_id": "$metadata.asset_type", "n": {"$sum": 1}}}]
    for r in dst[mongo.PRICE_HISTORY].aggregate(pipeline):
        print(f"  {str(r['_id']):<8} {r['n']:>12,}")

    return ok


def run(*, yes: bool, dry_run: bool) -> int:
    if LEGACY_DB_NAME not in mongo.client().list_database_names():
        print(f"{LEGACY_DB_NAME} is already absent — nothing to drop.")
        return 0

    legacy = mongo.market_analysis_db()
    legacy_count = sum(
        legacy[c].estimated_document_count() for c in legacy.list_collection_names()
    )
    print(f"Legacy DB '{LEGACY_DB_NAME}' currently holds {legacy_count:,} docs across "
          f"{len(legacy.list_collection_names())} collections.")
    print()

    ok = _preflight()
    if not ok:
        print()
        print("Pre-flight FAILED — refusing to drop the legacy DB.")
        return 1

    if dry_run:
        print()
        print("DRY RUN — would drop the legacy DB now (skipped).")
        return 0

    if not yes:
        print()
        confirm = input(
            f"Type 'DROP {LEGACY_DB_NAME}' to proceed: "
        ).strip()
        if confirm != f"DROP {LEGACY_DB_NAME}":
            print("Aborted.")
            return 1

    mongo.client().drop_database(LEGACY_DB_NAME)
    print(f"\nDropped {LEGACY_DB_NAME}.")

    if LEGACY_DB_NAME in mongo.client().list_database_names():
        print(f"WARNING: {LEGACY_DB_NAME} still appears in list_database_names()")
        return 2
    print(f"Verified: {LEGACY_DB_NAME} is gone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--yes", action="store_true",
                    help="Skip the interactive confirmation.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Pre-flight only; do not drop the DB.")
    args = ap.parse_args(argv)
    return run(yes=args.yes, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
