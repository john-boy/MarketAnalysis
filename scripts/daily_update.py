"""CLI: run the daily incremental update.

Usage::

    python -m scripts.daily_update              # all tracked ETFs
    python -m scripts.daily_update SPY QQQ      # a subset
    python -m scripts.daily_update --skip-indicators

Intended for the scheduler (market close + 2h) and for manual runs.
For initial ETF loading, use ``scripts/ingest_etf.py`` instead.
"""

from __future__ import annotations

import argparse
import logging
import sys

from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.sources.alpha_vantage import AlphaVantageClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("etfs", nargs="*",
                    help="ETF symbols to refresh (default: every tracked ETF).")
    ap.add_argument("--skip-prices", action="store_true")
    ap.add_argument("--skip-indicators", action="store_true")
    ap.add_argument("--cache", action="store_true",
                    help="Enable AV disk cache (dev replays).")
    ap.add_argument("--limit-symbols", type=int, default=None,
                    help="Cap the union symbol set (for test runs).")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    etf_symbols = [s.upper() for s in args.etfs] if args.etfs else None
    av = AlphaVantageClient(cache=args.cache)

    report = daily_svc.daily_update(
        etf_symbols=etf_symbols,
        client=av,
        progress=print,
        skip_prices=args.skip_prices,
        skip_indicators=args.skip_indicators,
        limit_symbols=args.limit_symbols,
    )
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
