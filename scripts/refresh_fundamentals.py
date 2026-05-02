"""CLI: refresh AV OVERVIEW fundamentals across many symbols.

Usage::

    python -m scripts.refresh_fundamentals               # every company
    python -m scripts.refresh_fundamentals AAPL MSFT     # a subset
    python -m scripts.refresh_fundamentals --limit 50    # cap for test runs

Per-symbol AV errors (rate-limit, unsupported tickers) are recorded
but never abort the loop.
"""

from __future__ import annotations

import argparse
import logging
import sys

from market_analysis.services.ingestors import fundamentals as fx
from market_analysis.sources.alpha_vantage import AlphaVantageClient


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbols", nargs="*",
                    help="Symbols to refresh (default: every company).")
    ap.add_argument("--cache", action="store_true",
                    help="Enable AV disk cache (dev replays).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Cap the symbol list (after dedup/sort).")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    symbols = [s.upper() for s in args.symbols] if args.symbols else None
    av = AlphaVantageClient(cache=args.cache)
    report = fx.ingest_fundamentals_batch(
        symbols, client=av, progress=print, limit=args.limit,
    )
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
