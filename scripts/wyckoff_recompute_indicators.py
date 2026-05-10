"""Recompute and persist indicators against the migrated adj_close data.

PR 3c of the WyckoffDB transition. The 6.36M legacy indicator docs in
MarketAnalysis are not migrated (per WYCKOFF_CODE_SPEC.md Change 7).
This script populates the empty ``WyckoffDB.indicators`` TSC by
calling ``panel.recompute_panel(..., mode="full")`` over every symbol
present in ``price_history``. The panel loader prefers ``adj_close``,
so EMAs / RSIs are continuous across split boundaries.

Default scope: every distinct symbol in ``price_history``. Pass
``--watchlist-only`` to limit to actively-tracked symbols.

Usage::

    python -m scripts.wyckoff_recompute_indicators --dry-run
    python -m scripts.wyckoff_recompute_indicators --watchlist-only
    python -m scripts.wyckoff_recompute_indicators
    python -m scripts.wyckoff_recompute_indicators --batch-size 50

After daily ingestion has run a few cycles, re-running this script is
not necessary; daily ingestion's incremental indicator path keeps the
collection current.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field

from market_analysis.data import mongo
from market_analysis.services import panel as panel_svc


log = logging.getLogger(__name__)


@dataclass
class Report:
    symbols: int = 0
    batches: int = 0
    inserted: int = 0
    failures: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


def _all_price_symbols() -> list[str]:
    return sorted(s for s in mongo.price_history().distinct("metadata.symbol") if s)


def _watchlist_symbols() -> list[str]:
    return sorted(
        d["symbol"]
        for d in mongo.watchlist().find({}, {"symbol": 1, "_id": 0})
        if d.get("symbol")
    )


def run(*, watchlist_only: bool, batch_size: int, dry_run: bool) -> Report:
    report = Report()
    start = time.monotonic()

    symbols = _watchlist_symbols() if watchlist_only else _all_price_symbols()
    report.symbols = len(symbols)
    print(f"Recomputing indicators for {len(symbols)} symbol(s) in batches of {batch_size}")

    if dry_run:
        print("DRY RUN — no compute, no writes.")
        for s in symbols[:20]:
            print(f"  would recompute {s}")
        if len(symbols) > 20:
            print(f"  ... and {len(symbols) - 20} more")
        return report

    # mode="full" = drop existing (indicator,stack) docs for each batch's
    # symbols, then write fresh. Empty indicators TSC means nothing to drop.
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        report.batches += 1
        try:
            r = panel_svc.recompute_panel(batch, mode="full")
        except Exception as e:  # noqa: BLE001
            msg = f"batch {report.batches} ({len(batch)} symbols): {e}"
            log.exception(msg)
            report.failures.append(msg)
            continue
        n = r.total_inserted()
        report.inserted += n
        elapsed = time.monotonic() - start
        rate = (i + len(batch)) / elapsed if elapsed else 0
        print(
            f"  batch {report.batches:>3}/{(len(symbols) + batch_size - 1) // batch_size}: "
            f"+{n:>7,} indicator docs  "
            f"({i + len(batch):>4}/{len(symbols)} symbols, "
            f"{rate:.1f} sym/s)"
        )

    report.elapsed_sec = time.monotonic() - start
    return report


def _print_summary(report: Report) -> None:
    print("\n=== Summary ===")
    print(f"  Symbols processed:     {report.symbols}")
    print(f"  Batches:               {report.batches}")
    print(f"  Indicator docs written: {report.inserted:,}")
    print(f"  Failures:              {len(report.failures)}")
    print(f"  Elapsed:               {report.elapsed_sec:.1f}s")
    if report.failures:
        print("\n  Failed batches:")
        for f in report.failures:
            print(f"    {f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--watchlist-only", action="store_true",
        help="Limit recompute to watchlist symbols (otherwise: every "
             "symbol present in price_history).",
    )
    ap.add_argument(
        "--batch-size", type=int, default=50,
        help="Symbols per recompute_panel call (default 50). The panel "
             "is loaded once per batch, so larger batches are more "
             "memory-intensive but reduce per-symbol overhead.",
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen; make no changes.",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    report = run(
        watchlist_only=args.watchlist_only,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
