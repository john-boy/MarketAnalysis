"""Backfill ``splits_events`` from the AlphaVantage SPLITS endpoint.

PR 3b of the WyckoffDB transition (see WYCKOFF_CODE_SPEC.md Change 6
and WYCKOFF_TRANSITION_CHECKLIST.md Phase 5). One-time backfill — not
called from daily ingestion (splits are rare events).

For each equity symbol present in ``price_history``:

  1. Call AV ``SPLITS`` (1 API call per symbol).
  2. Parse split events, compute ``cumulative_factor`` forward in time.
  3. Upsert into ``splits_events`` keyed on ``(symbol, effective_date)``.

Rate-limit: free tier = 25 req/day, premium = 75 req/min. Configured
via the AV client's existing rate limiter.

Usage::

    python -m scripts.wyckoff_backfill_splits --dry-run
    python -m scripts.wyckoff_backfill_splits --watchlist-only
    python -m scripts.wyckoff_backfill_splits

After the cutover this script can be deleted; daily ingestion does
not refresh splits.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from market_analysis.data import mongo
from market_analysis.sources.alpha_vantage import (
    AlphaVantageClient,
    AlphaVantageError,
)


log = logging.getLogger(__name__)


@dataclass
class Report:
    symbols_attempted: int = 0
    symbols_with_splits: int = 0
    events_written: int = 0
    failures: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


def _equity_symbols_from_price_history() -> list[str]:
    """Return distinct symbols whose price_history docs are tagged equity."""
    syms = mongo.price_history().distinct(
        "metadata.symbol",
        {"metadata.asset_type": "equity"},
    )
    return sorted(s for s in syms if s)


def _watchlist_equity_symbols() -> list[str]:
    """Return watchlist symbols whose first price_history bar is equity-typed."""
    out: list[str] = []
    for d in mongo.watchlist().find({}, {"symbol": 1, "_id": 0}).sort("symbol", 1):
        sym = d.get("symbol")
        if not sym:
            continue
        bar = mongo.price_history().find_one(
            {"metadata.symbol": sym}, {"metadata.asset_type": 1, "_id": 0},
        )
        if bar and (bar.get("metadata") or {}).get("asset_type") == "equity":
            out.append(sym)
    return out


def _parse_av_splits(symbol: str, payload: dict) -> list[dict]:
    """Translate an AV SPLITS payload into splits_events docs.

    AV returns most-recent-first; reverse so cumulative_factor compounds
    forward in time (oldest split first).
    """
    raw = payload.get("data") or []
    events: list[dict] = []
    cumulative = 1.0
    now = datetime.now(timezone.utc)

    for item in reversed(raw):
        try:
            factor = float(item.get("split_factor") or 1.0)
            eff = item.get("effective_date")
            eff_dt = datetime.strptime(eff, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError) as e:
            log.warning("  %s: skipping malformed split event %r: %s", symbol, item, e)
            continue
        cumulative *= factor
        events.append({
            "symbol": symbol,
            "effective_date": eff_dt,
            "split_factor": factor,
            "cumulative_factor": round(cumulative, 8),
            "ingested_at": now,
        })
    return events


def _upsert_events(events: list[dict]) -> int:
    if not events:
        return 0
    coll = mongo.splits_events()
    n = 0
    for ev in events:
        coll.update_one(
            {"symbol": ev["symbol"], "effective_date": ev["effective_date"]},
            {"$set": ev},
            upsert=True,
        )
        n += 1
    return n


def run(*, watchlist_only: bool, dry_run: bool) -> Report:
    report = Report()
    start = time.monotonic()

    if watchlist_only:
        symbols = _watchlist_equity_symbols()
    else:
        symbols = _equity_symbols_from_price_history()

    print(f"Symbols to backfill: {len(symbols)}")
    if dry_run:
        print("DRY RUN — no AV calls, no writes.")
        for s in symbols[:20]:
            print(f"  would call SPLITS({s})")
        if len(symbols) > 20:
            print(f"  ... and {len(symbols) - 20} more")
        return report

    av = AlphaVantageClient()
    for i, sym in enumerate(symbols, 1):
        report.symbols_attempted += 1
        try:
            payload = av.splits(sym)
        except AlphaVantageError as e:
            msg = f"{sym}: AV error: {e}"
            log.warning("  %s", msg)
            report.failures.append(msg)
            continue
        except Exception as e:  # noqa: BLE001
            msg = f"{sym}: unexpected: {e}"
            log.exception("  %s", msg)
            report.failures.append(msg)
            continue

        events = _parse_av_splits(sym, payload)
        if events:
            report.symbols_with_splits += 1
            n = _upsert_events(events)
            report.events_written += n
            print(f"  [{i:>4}/{len(symbols)}] {sym}: {n} split event(s)")
        else:
            print(f"  [{i:>4}/{len(symbols)}] {sym}: no splits")

    report.elapsed_sec = time.monotonic() - start
    return report


def _print_summary(report: Report) -> None:
    print("\n=== Summary ===")
    print(f"  Symbols attempted:     {report.symbols_attempted}")
    print(f"  Symbols with splits:   {report.symbols_with_splits}")
    print(f"  Total events written:  {report.events_written}")
    print(f"  Failures:              {len(report.failures)}")
    print(f"  Elapsed:               {report.elapsed_sec:.1f}s")
    if report.failures:
        print("\n  Failed symbols:")
        for f in report.failures:
            print(f"    {f}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--watchlist-only", action="store_true",
        help="Backfill only symbols on the watchlist (otherwise: every "
             "equity in price_history).",
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would happen; make no AV calls.")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    report = run(watchlist_only=args.watchlist_only, dry_run=args.dry_run)
    if not args.dry_run:
        _print_summary(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
