"""Migrate MarketAnalysis.daily_quotes -> WyckoffDB.price_history.

PR 2 of the WyckoffDB transition (see docs/WYCKOFF_CODE_SPEC.md and
docs/WYCKOFF_TRANSITION_CHECKLIST.md Phase 3).

For every doc in ``daily_quotes``:

  1. Compute split-adjusted OHLCV via
     :func:`market_analysis.services.ingestors._common.compute_adj_fields`.
  2. Set ``metadata.asset_type`` to "etf" / "index" / "equity" based on
     a one-pass lookup against the etf and watchlist collections (with
     a fallback heuristic: missing volume -> "index").
  3. Insert into ``price_history`` (TSC, append-only).

Idempotent only at the collection-replace level: if ``price_history``
already has documents this script refuses to run unless ``--force`` is
supplied (which drops and recreates the TSC before re-migrating).

Usage::

    python -m scripts.wyckoff_migrate_prices --dry-run
    python -m scripts.wyckoff_migrate_prices
    python -m scripts.wyckoff_migrate_prices --force      # drop & redo
    python -m scripts.wyckoff_migrate_prices --limit 5000 # smoke test

After verification (Phase 3 spot-checks pass) and cutover, this script
should be deleted.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from market_analysis.data import mongo
from market_analysis.services.ingestors._common import compute_adj_fields


BATCH_SIZE = 5000           # docs per insert_many
PROGRESS_EVERY = 100_000


@dataclass
class Report:
    source_count: int = 0
    inserted: int = 0
    asset_type_counts: dict[str, int] = field(default_factory=dict)
    spot_checks: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


def _build_asset_type_lookup() -> dict[str, str]:
    """Return ``{symbol: asset_type}`` from etf + watchlist collections.

    Symbols not present here default to "equity" (with a "no volume"
    fallback to "index" applied per-doc downstream).
    """
    out: dict[str, str] = {}

    # Source collections live under the legacy MarketAnalysis DB.
    src = mongo.market_analysis_db()
    for d in src[mongo.ETF].find({}, {"symbol": 1, "_id": 0}):
        if sym := d.get("symbol"):
            out[sym.upper()] = "etf"
    for d in src[mongo.WATCHLIST].find(
        {}, {"symbol": 1, "ingest_tags": 1, "_id": 0}
    ):
        sym = (d.get("symbol") or "").upper()
        if not sym:
            continue
        tags = {t.lower() for t in (d.get("ingest_tags") or [])}
        if "index" in tags:
            out[sym] = "index"

    return out


def _derive_asset_type(symbol: str, doc: dict, lookup: dict[str, str]) -> str:
    if t := lookup.get((symbol or "").upper()):
        return t
    # Heuristic for indices not registered in watchlist (e.g. VIX historical).
    if doc.get("volume") is None and doc.get("adjusted_close") is None:
        return "index"
    return "equity"


def _drop_and_recreate_tsc(dst) -> None:
    if mongo.PRICE_HISTORY in dst.list_collection_names():
        dst.drop_collection(mongo.PRICE_HISTORY)
        print(f"  dropped {mongo.PRICE_HISTORY} (--force)")
    dst.create_collection(
        mongo.PRICE_HISTORY,
        timeseries=mongo.TIMESERIES_OPTIONS[mongo.PRICE_HISTORY],
    )
    # Recreate the explicit indexes from wyckoff_db_setup.
    from scripts.wyckoff_db_setup import INDEXES

    for keys, opts in INDEXES[mongo.PRICE_HISTORY]:
        dst[mongo.PRICE_HISTORY].create_index(keys, **opts)
    print(f"  recreated {mongo.PRICE_HISTORY} (TSC + indexes)")


def _migrate(*, force: bool, dry_run: bool, limit: int | None) -> Report:
    src = mongo.market_analysis_db()
    dst = mongo.db()                   # WyckoffDB
    report = Report()
    start = time.monotonic()

    src_col = src["daily_quotes"]
    tgt_col = dst[mongo.PRICE_HISTORY]

    report.source_count = src_col.estimated_document_count()
    existing = tgt_col.estimated_document_count()

    print(f"Source:  {src.name}.daily_quotes  ({report.source_count:,} docs)")
    print(f"Target:  {dst.name}.{mongo.PRICE_HISTORY}  ({existing:,} docs)")
    if limit:
        print(f"Limit:   first {limit:,} docs")
    print()

    if dry_run:
        print("DRY RUN — no changes will be made.")
        return report

    if existing > 0:
        if not force:
            print(
                f"ERROR: target already has {existing:,} docs. "
                "Rerun with --force to drop and re-migrate."
            )
            sys.exit(1)
        _drop_and_recreate_tsc(dst)
        tgt_col = dst[mongo.PRICE_HISTORY]

    asset_lookup = _build_asset_type_lookup()
    print(f"Asset-type lookup: {len(asset_lookup)} symbols pre-classified")
    print()

    cursor = src_col.find({}, {"_id": 0})
    if limit:
        cursor = cursor.limit(limit)

    batch: list[dict] = []
    processed = 0
    type_counts: dict[str, int] = {"equity": 0, "etf": 0, "index": 0}

    for doc in cursor:
        symbol = (doc.get("metadata") or {}).get("symbol") or "UNKNOWN"
        asset_type = _derive_asset_type(symbol, doc, asset_lookup)
        type_counts[asset_type] = type_counts.get(asset_type, 0) + 1

        adj = compute_adj_fields(doc)

        new_doc = {
            "date": doc["date"],
            "metadata": {
                "symbol": symbol,
                "source": (doc.get("metadata") or {}).get("source", "alpha_vantage"),
                "asset_type": asset_type,
            },
            # Raw AV fields
            "open": doc.get("open"),
            "high": doc.get("high"),
            "low": doc.get("low"),
            "close": doc.get("close"),
            "volume": doc.get("volume"),
            "adjusted_close": doc.get("adjusted_close"),
            "split_coefficient": doc.get("split_coefficient"),
            "dividend": doc.get("dividend"),
            # Computed adjusted fields
            **adj,
        }
        batch.append(new_doc)
        processed += 1

        if len(batch) >= BATCH_SIZE:
            tgt_col.insert_many(batch, ordered=False)
            report.inserted += len(batch)
            batch.clear()
            if processed % PROGRESS_EVERY == 0:
                elapsed = time.monotonic() - start
                rate = processed / elapsed if elapsed else 0
                print(
                    f"  {processed:>10,} / {report.source_count:,}  "
                    f"({rate:>6,.0f} docs/s)"
                )

    if batch:
        tgt_col.insert_many(batch, ordered=False)
        report.inserted += len(batch)

    report.asset_type_counts = type_counts
    report.elapsed_sec = time.monotonic() - start
    return report


# -- Verification ---------------------------------------------------------


def _verify(report: Report) -> None:
    dst = mongo.wyckoff_db()
    tgt_col = dst[mongo.PRICE_HISTORY]

    print("\n=== Verification ===")
    n = tgt_col.estimated_document_count()
    print(f"  price_history total docs: {n:,} (source was {report.source_count:,})")

    print("\n  Asset-type distribution (from migration):")
    for k, v in sorted(report.asset_type_counts.items()):
        print(f"    {k:<8} {v:>10,}")

    spot_checks: list[tuple[str, str, datetime, float | None]] = [
        # (symbol, label, date, expected_adj_factor_or_None)
        ("AAPL", "pre-4:1 split (2020-08-28)", datetime(2020, 8, 28), 0.25),
        ("AAPL", "pre-7:1 split (2014-06-06)", datetime(2014, 6, 6), 0.0357),
        ("AAPL", "current (2026-05-08)",       datetime(2026, 5, 8), 1.0),
        ("VIX",  "index (2026-05-08)",         datetime(2026, 5, 8), 1.0),
    ]
    print("\n  Spot checks:")
    for sym, label, date, expected in spot_checks:
        d = tgt_col.find_one({"metadata.symbol": sym, "date": date})
        if not d:
            line = f"    {sym:<6} {label:<30} MISSING"
        else:
            f = d.get("adj_factor")
            ok = "PASS" if (expected is None or abs(f - expected) / max(expected, 1e-9) < 0.05) else "FAIL"
            ao = d.get("adj_open")
            line = (f"    {sym:<6} {label:<30} adj_factor={f:.6f} "
                    f"adj_open={ao}  [{ok}]")
        print(line)
        report.spot_checks.append(line.strip())


def _print_summary(report: Report) -> None:
    print("\n=== Summary ===")
    print(f"  Source docs:    {report.source_count:>12,}")
    print(f"  Inserted:       {report.inserted:>12,}")
    diff = report.source_count - report.inserted
    print(f"  Difference:     {diff:>12,}  (should be 0)")
    print(f"  Elapsed:        {report.elapsed_sec:>12.2f}s")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="Drop existing price_history and re-migrate.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen; make no changes.")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process only the first N docs (smoke test).")
    args = ap.parse_args(argv)

    report = _migrate(force=args.force, dry_run=args.dry_run, limit=args.limit)
    if not args.dry_run:
        _verify(report)
        _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
