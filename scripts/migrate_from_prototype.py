"""Migrate MarketAnalysis_Prototype → MarketAnalysis.

Copies ``daily_quotes`` and ``indicators`` forward from the prototype
database (expensive to rebuild), creates all new collections with
indexes, writes ``schema_version = 1``.

The script is idempotent at the level of the copy-forward
collections: if either is already populated in the target DB, the
script refuses to run unless ``--force`` is supplied (which drops
those collections first).  Empty target DB → safe to run.

Usage::

    python -m scripts.migrate_from_prototype
    python -m scripts.migrate_from_prototype --dry-run
    python -m scripts.migrate_from_prototype --force
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field

from pymongo import ASCENDING, DESCENDING
from pymongo.collection import Collection
from pymongo.database import Database

from market_analysis.data import mongo
from market_analysis.services.config import get_settings


SCHEMA_VERSION_VALUE = 1

#: Collections carried over from the prototype (``_id`` preserved).
COPY_FORWARD: tuple[str, ...] = (mongo.DAILY_QUOTES, mongo.INDICATORS)

#: Indexes to create on the target DB.
#: Map ``collection_name → list[(keys, options)]``.
#: Note: time-series collections (``daily_quotes``, ``indicators``) do not
#: support unique indexes; secondary indexes on the real field paths.
INDEXES: dict[str, list[tuple[list[tuple[str, int]], dict]]] = {
    mongo.DAILY_QUOTES: [
        ([("metadata.symbol", ASCENDING), ("date", ASCENDING)],
         {"name": "metadata_symbol_date"}),
    ],
    mongo.INDICATORS: [
        ([("metadata.symbol", ASCENDING),
          ("metadata.indicator", ASCENDING),
          ("date", ASCENDING)],
         {"name": "metadata_symbol_indicator_date"}),
    ],
    mongo.COMPANIES: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.ETF: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.INDEXES: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.THEME_GROUPS: [
        ([("name", ASCENDING)], {"unique": True, "name": "name"}),
    ],
    mongo.THEMES: [
        ([("name", ASCENDING)], {"unique": True, "name": "name"}),
    ],
    mongo.THEME_DOCUMENTS: [
        ([("path", ASCENDING)], {"unique": True, "name": "path"}),
    ],
    mongo.WATCHLIST: [
        ([("symbol", ASCENDING)], {"unique": True, "name": "symbol"}),
    ],
    mongo.ACCOUNTS: [
        ([("nickname", ASCENDING)], {"unique": True, "name": "nickname"}),
    ],
    mongo.POSITIONS: [
        ([("account_id", ASCENDING), ("symbol", ASCENDING)],
         {"unique": True, "name": "account_symbol"}),
    ],
    mongo.ACCOUNT_SYNC_LOG: [
        ([("account_id", ASCENDING), ("started_at", DESCENDING)],
         {"name": "account_started"}),
    ],
    mongo.FILINGS: [
        ([("cik", ASCENDING), ("filing_date", DESCENDING)],
         {"name": "cik_filing_date"}),
        ([("accession_number", ASCENDING)],
         {"unique": True, "name": "accession_number"}),
    ],
    mongo.NEWS: [
        ([("symbol", ASCENDING), ("published_at", DESCENDING)],
         {"name": "symbol_published_at"}),
    ],
    mongo.PIPELINE_DEFINITIONS: [],
    mongo.SCHEMA_VERSION: [],
}


@dataclass
class Report:
    copied: dict[str, int] = field(default_factory=dict)
    created: list[str] = field(default_factory=list)
    indexes: dict[str, list[str]] = field(default_factory=dict)
    seeded: dict[str, list[str]] = field(default_factory=dict)
    schema_version: int | None = None
    elapsed_sec: float = 0.0


# -- Helpers --------------------------------------------------------------


def _copy_targets_with_data(dst: Database) -> list[str]:
    """Return the copy-forward collections that already hold documents."""
    existing = set(dst.list_collection_names())
    hits: list[str] = []
    for name in COPY_FORWARD:
        if name in existing and dst[name].estimated_document_count() > 0:
            hits.append(name)
    return hits


def _copy_targets_wrong_type(dst: Database) -> list[str]:
    """Return copy-forward collections that exist but are not time-series.

    If a prior failed run created them as regular collections, we must
    drop and recreate (even if empty) before re-copying.
    """
    wrong: list[str] = []
    for name in COPY_FORWARD:
        info = dst.command("listCollections", filter={"name": name})
        for c in info["cursor"]["firstBatch"]:
            if c.get("type") != "timeseries":
                wrong.append(name)
    return wrong


def _copy_collection(src: Collection, dst: Collection, batch: int = 5000) -> int:
    """Copy every document from ``src`` to ``dst``, preserving ``_id``."""
    count = 0
    buf: list[dict] = []
    for doc in src.find({}):
        buf.append(doc)
        if len(buf) >= batch:
            dst.insert_many(buf, ordered=False)
            count += len(buf)
            buf.clear()
    if buf:
        dst.insert_many(buf, ordered=False)
        count += len(buf)
    return count


# -- Main -----------------------------------------------------------------


def run(*, force: bool = False, dry_run: bool = False) -> Report:
    s = get_settings()
    report = Report()
    start = time.monotonic()

    print(f"Source DB:  {s.mongo.prototype_database}")
    print(f"Target DB:  {s.mongo.database}")
    print(f"Mongo URI:  {s.mongo.uri}")
    print()

    # Pre-flight ----------------------------------------------------------
    db_names = mongo.client().list_database_names()
    if s.mongo.prototype_database not in db_names:
        print(f"ERROR: prototype database '{s.mongo.prototype_database}' not found.")
        sys.exit(2)

    src = mongo.prototype_db()
    src_names = set(src.list_collection_names())
    missing = [c for c in COPY_FORWARD if c not in src_names]
    if missing:
        print(f"ERROR: prototype missing expected collection(s): {missing}")
        sys.exit(2)

    dst = mongo.db()
    conflicts = _copy_targets_with_data(dst)
    wrong_type = _copy_targets_wrong_type(dst)

    if conflicts and not force:
        print(f"Target already has data in: {conflicts}")
        print("Rerun with --force to drop those collections and re-copy.")
        sys.exit(1)
    if wrong_type and not force:
        print(f"Target collections are regular (not time-series): {wrong_type}")
        print("Rerun with --force to drop and recreate as time-series.")
        sys.exit(1)

    if dry_run:
        print("DRY RUN — no changes will be made.")
        for name in COPY_FORWARD:
            n = src[name].estimated_document_count()
            print(f"  would copy {name}: ~{n:,} docs")
        for name in mongo.ALL_COLLECTIONS:
            if name not in dst.list_collection_names():
                print(f"  would create {name}")
        return report

    # 1. Drop conflicts / wrong-type collections --------------------------
    to_drop = set(conflicts) | set(wrong_type)
    if force and to_drop:
        for name in sorted(to_drop):
            dst.drop_collection(name)
            print(f"  dropped {name} (--force)")
        print()

    # 2. Create time-series collections explicitly before the copy --------
    # Otherwise Mongo auto-creates them as regular collections on insert.
    for name, opts in mongo.TIMESERIES_OPTIONS.items():
        if name not in dst.list_collection_names():
            dst.create_collection(name, timeseries=opts)
            report.created.append(f"{name} (timeseries)")

    # 3. Copy forward -----------------------------------------------------
    for name in COPY_FORWARD:
        print(f"Copying {name}...")
        n = _copy_collection(src[name], dst[name])
        report.copied[name] = n
        print(f"  copied {n:,} docs")
    print()

    # 4. Create remaining (regular) collections + indexes ----------------
    for name in mongo.ALL_COLLECTIONS:
        if name not in dst.list_collection_names():
            dst.create_collection(name)
            report.created.append(name)

        created_indexes: list[str] = []
        for keys, opts in INDEXES.get(name, []):
            idx_name = dst[name].create_index(keys, **opts)
            created_indexes.append(idx_name)
        if created_indexes:
            report.indexes[name] = created_indexes

    # 4. Schema version ---------------------------------------------------
    dst[mongo.SCHEMA_VERSION].update_one(
        {"_id": "schema_version"},
        {"$set": {
            "version": SCHEMA_VERSION_VALUE,
            "applied_at": time.time(),
        }},
        upsert=True,
    )
    report.schema_version = SCHEMA_VERSION_VALUE

    # 5. Seed domain fixtures --------------------------------------------
    # Imported lazily so this script stays usable if seeders are broken.
    from scripts import seed_accounts, seed_indexes

    report.seeded["accounts"] = seed_accounts.run()["inserted"]
    report.seeded["indexes"] = seed_indexes.run()["inserted"]

    report.elapsed_sec = time.monotonic() - start
    return report


def _print_summary(report: Report) -> None:
    print("=== Summary ===")
    for name, n in report.copied.items():
        print(f"  Copied   {name:<22} {n:>10,} docs")
    for name in report.created:
        print(f"  Created  {name}")
    for name, idx in report.indexes.items():
        print(f"  Indexed  {name:<22} {', '.join(idx)}")
    for coll, names in report.seeded.items():
        if names:
            print(f"  Seeded   {coll:<22} {', '.join(names)}")
        else:
            print(f"  Seeded   {coll:<22} (already present)")
    if report.schema_version is not None:
        print(f"  Schema version:        {report.schema_version}")
    print(f"  Elapsed:               {report.elapsed_sec:.2f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="Drop non-empty copy-forward collections first.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen; make no changes.")
    args = ap.parse_args(argv)

    report = run(force=args.force, dry_run=args.dry_run)
    print()
    _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
