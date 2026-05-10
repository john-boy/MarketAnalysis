"""Create WyckoffDB collections, indexes, and migrate reference data.

One-shot transition script for the MarketAnalysis → WyckoffDB migration
(see ``docs/WYCKOFF_CODE_SPEC.md`` and ``docs/WYCKOFF_TRANSITION_CHECKLIST.md``).

Steps:
  1. Create ``price_history`` as a time-series collection on WyckoffDB.
  2. Create the regular collections: companies, etf, watchlist,
     splits_events, features, phase_labels, transitions, projections.
  3. Build all indexes per spec.
  4. Migrate companies and etf as-is from MarketAnalysis.
  5. Migrate watchlist, adding ``wyckoff_priority=3`` and
     ``last_phase_checked=None`` per the spec.
  6. Print a verification report (collection counts + index names).

This script does NOT migrate ``daily_quotes`` → ``price_history``; that
runs separately via ``scripts.wyckoff_migrate_prices``.

Usage::

    python -m scripts.wyckoff_db_setup --dry-run
    python -m scripts.wyckoff_db_setup
    python -m scripts.wyckoff_db_setup --force      # drop & recreate refs

After cutover, this script and the rest of the migration toolkit should
be deleted.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from market_analysis.data import mongo


#: Indexes per WyckoffDB collection. Mirrors WYCKOFF_CODE_SPEC.md § Schema.
INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    mongo.PRICE_HISTORY: [
        ([("metadata.symbol", ASCENDING), ("date", ASCENDING)],
         {"name": "symbol_date"}),
        ([("date", DESCENDING)],
         {"name": "date_desc"}),
    ],
    mongo.COMPANIES: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.ETF: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.WATCHLIST: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.INDEXES: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.INDICATORS: [
        ([("metadata.symbol", ASCENDING),
          ("metadata.indicator", ASCENDING),
          ("date", ASCENDING)],
         {"name": "metadata_symbol_indicator_date"}),
    ],
    mongo.SPLITS_EVENTS: [
        ([("symbol", ASCENDING), ("effective_date", ASCENDING)],
         {"unique": True, "name": "symbol_effective_date"}),
    ],
    mongo.FEATURES: [
        ([("symbol", ASCENDING), ("feature_set", ASCENDING),
          ("date", ASCENDING)],
         {"unique": True, "name": "symbol_feature_date"}),
    ],
    mongo.PHASE_LABELS: [
        ([("symbol", ASCENDING), ("date", ASCENDING)],
         {"unique": True, "name": "symbol_date"}),
        ([("symbol", ASCENDING), ("phase", ASCENDING)],
         {"name": "symbol_phase"}),
        ([("phase", ASCENDING), ("date", DESCENDING)],
         {"name": "phase_date"}),
    ],
    mongo.TRANSITIONS: [
        ([("symbol", ASCENDING), ("detection_date", DESCENDING)],
         {"name": "symbol_detection_date"}),
        ([("symbol", ASCENDING), ("is_confirmed", ASCENDING)],
         {"name": "symbol_is_confirmed"}),
    ],
    mongo.PROJECTIONS: [
        ([("symbol", ASCENDING), ("projection_date", DESCENDING)],
         {"name": "symbol_projection_date"}),
        ([("symbol", ASCENDING), ("current_phase", ASCENDING),
          ("projection_date", DESCENDING)],
         {"name": "symbol_phase_projection_date"}),
    ],
}

#: Reference collections to migrate from MarketAnalysis.
#: ``indexes`` is included as a pragmatic carryover (spec said don't migrate,
#: but index ingestion would otherwise break — see docs/decisions.md).
REFERENCE_COLLECTIONS: tuple[str, ...] = (
    mongo.COMPANIES,
    mongo.ETF,
    mongo.WATCHLIST,
    mongo.INDEXES,
)


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    indexes: dict[str, list[str]] = field(default_factory=dict)
    migrated: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    elapsed_sec: float = 0.0


# -- Helpers --------------------------------------------------------------


def _existing_collections(dst: Database) -> set[str]:
    return set(dst.list_collection_names())


def _collection_type(dst: Database, name: str) -> str | None:
    info = dst.command("listCollections", filter={"name": name})
    for c in info["cursor"]["firstBatch"]:
        return c.get("type")
    return None


def _create_collections(dst: Database, *, force: bool, report: Report) -> None:
    existing = _existing_collections(dst)

    # Time-series collections must be created explicitly (otherwise Mongo
    # auto-creates them as regular collections on first insert).
    for name in mongo.TIMESERIES_OPTIONS:
        if name not in mongo.WYCKOFF_COLLECTIONS:
            continue
        if name in existing:
            existing_type = _collection_type(dst, name)
            if existing_type != "timeseries":
                if not force:
                    print(
                        f"ERROR: {name} exists but is not time-series "
                        f"(type={existing_type}). Rerun with --force to recreate."
                    )
                    sys.exit(1)
                dst.drop_collection(name)
                existing.discard(name)
                print(f"  dropped {name} (--force, wrong type)")
        if name not in existing:
            dst.create_collection(name, timeseries=mongo.TIMESERIES_OPTIONS[name])
            report.created.append(f"{name} (timeseries)")

    # Regular collections
    for name in mongo.WYCKOFF_COLLECTIONS:
        if name in mongo.TIMESERIES_OPTIONS:
            continue
        if name not in existing:
            dst.create_collection(name)
            report.created.append(name)


def _build_indexes(dst: Database, report: Report) -> None:
    for col_name, indexes in INDEXES.items():
        names: list[str] = []
        for keys, opts in indexes:
            try:
                idx_name = dst[col_name].create_index(keys, **opts)
                names.append(idx_name)
            except Exception as e:
                print(f"  WARN: index {col_name}.{opts.get('name')} skipped: {e}")
        if names:
            report.indexes[col_name] = names


def _migrate_reference(
    src: Database, dst: Database, *, force: bool, report: Report
) -> None:
    for name in REFERENCE_COLLECTIONS:
        target = dst[name]
        existing_count = target.estimated_document_count()
        if existing_count > 0:
            if not force:
                print(
                    f"  {name}: target already has {existing_count} docs "
                    f"— skipping (use --force to overwrite)"
                )
                continue
            target.delete_many({})
            print(f"  {name}: cleared {existing_count} existing docs (--force)")

        docs = list(src[name].find({}, {"_id": 0}))
        if not docs:
            print(f"  {name}: source empty — nothing to migrate")
            continue

        if name == mongo.WATCHLIST:
            for d in docs:
                d.setdefault("wyckoff_priority", 3)
                d.setdefault("last_phase_checked", None)

        target.insert_many(docs, ordered=False)
        report.migrated[name] = len(docs)
        print(f"  {name}: migrated {len(docs)} docs")


def _verify(dst: Database, report: Report) -> None:
    print("\n=== Verification ===")
    print(f"{'Collection':<22} {'Type':<12} {'Docs':>10}  Indexes")
    print("-" * 78)
    for name in mongo.WYCKOFF_COLLECTIONS:
        col_type = _collection_type(dst, name) or "missing"
        n = dst[name].estimated_document_count() if col_type != "missing" else 0
        report.counts[name] = n
        idx_names = sorted(dst[name].index_information().keys()) if col_type != "missing" else []
        print(f"{name:<22} {col_type:<12} {n:>10,}  {', '.join(idx_names)}")


# -- Main -----------------------------------------------------------------


def run(*, force: bool = False, dry_run: bool = False) -> Report:
    src = mongo.market_analysis_db()   # legacy DB (read-only here)
    dst = mongo.db()                   # WyckoffDB (settings.toml default)
    report = Report()
    start = time.monotonic()

    print(f"Source DB:  {src.name}")
    print(f"Target DB:  {dst.name}")
    print()

    if dry_run:
        print("DRY RUN — no changes will be made.\n")
        existing = _existing_collections(dst)
        for name in mongo.WYCKOFF_COLLECTIONS:
            mark = "exists" if name in existing else "would create"
            print(f"  {mark:<14} {name}")
        print()
        for name in REFERENCE_COLLECTIONS:
            n = src[name].estimated_document_count()
            print(f"  would migrate {name}: ~{n:,} docs")
        return report

    print("Creating collections...")
    _create_collections(dst, force=force, report=report)

    print("\nBuilding indexes...")
    _build_indexes(dst, report)

    print("\nMigrating reference collections...")
    _migrate_reference(src, dst, force=force, report=report)

    _verify(dst, report)

    report.elapsed_sec = time.monotonic() - start
    return report


def _print_summary(report: Report) -> None:
    print("\n=== Summary ===")
    for name in report.created:
        print(f"  Created  {name}")
    for name, idx in report.indexes.items():
        print(f"  Indexed  {name:<22} {', '.join(idx)}")
    for name, n in report.migrated.items():
        print(f"  Migrated {name:<22} {n:>10,} docs")
    print(f"  Elapsed:                  {report.elapsed_sec:.2f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="Drop wrong-type collections and overwrite "
                         "non-empty reference collections.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen; make no changes.")
    args = ap.parse_args(argv)

    report = run(force=args.force, dry_run=args.dry_run)
    if not args.dry_run:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
