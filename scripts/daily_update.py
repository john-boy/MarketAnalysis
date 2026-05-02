"""CLI: run the daily incremental update.

Usage::

    python -m scripts.daily_update              # all tracked ETFs
    python -m scripts.daily_update SPY QQQ      # a subset
    python -m scripts.daily_update --skip-indicators
    python -m scripts.daily_update --skip-indexes
    python -m scripts.daily_update --no-log-file # skip logs/ output

Intended for the scheduler (market close + 2h) and for manual runs.
For initial ETF loading, use ``scripts/ingest_etf.py`` instead.

By default the script appends progress to ``logs/daily_update.log``
and Python warnings/errors to ``logs/daily_update.err``.  Each run
opens with a dated header banner so it's easy to tell runs apart in
the rolling file.
"""

from __future__ import annotations

import argparse
import logging
import sys

from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.services.run_logging import run_log
from market_analysis.sources.alpha_vantage import AlphaVantageClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("etfs", nargs="*",
                    help="ETF symbols to refresh (default: every tracked ETF).")
    ap.add_argument("--skip-prices", action="store_true")
    ap.add_argument("--skip-indicators", action="store_true")
    ap.add_argument("--skip-indexes", action="store_true",
                    help="Skip the tracked-index refresh step.")
    ap.add_argument("--cache", action="store_true",
                    help="Enable AV disk cache (dev replays).")
    ap.add_argument("--limit-symbols", type=int, default=None,
                    help="Cap the union symbol set (for test runs).")
    ap.add_argument("--log-level", default="INFO")
    ap.add_argument("--no-log-file", action="store_true",
                    help="Disable writing to logs/daily_update.{log,err}.")
    raw_argv = argv if argv is not None else sys.argv[1:]
    args = ap.parse_args(raw_argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    etf_symbols = [s.upper() for s in args.etfs] if args.etfs else None
    av = AlphaVantageClient(cache=args.cache)

    source = f"cli: {' '.join(raw_argv) if raw_argv else '(no args)'}"
    with run_log(
        "daily_update",
        source=source,
        extra_progress=print,
        log_level=args.log_level,
        enabled=not args.no_log_file,
    ) as progress:
        report = daily_svc.daily_update(
            etf_symbols=etf_symbols,
            client=av,
            progress=progress,
            skip_prices=args.skip_prices,
            skip_indicators=args.skip_indicators,
            skip_indexes=args.skip_indexes,
            limit_symbols=args.limit_symbols,
        )

    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
