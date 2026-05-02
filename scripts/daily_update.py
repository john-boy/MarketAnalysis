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
from datetime import datetime
from pathlib import Path
from typing import TextIO

from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.sources.alpha_vantage import AlphaVantageClient


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / "logs"
LOG_FILE = LOG_DIR / "daily_update.log"
ERR_FILE = LOG_DIR / "daily_update.err"


def _write_header(stream: TextIO, label: str, argv: list[str]) -> None:
    """Emit a dated banner so runs are visually separable in append mode."""
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    bar = "=" * 72
    stream.write(f"\n{bar}\n")
    stream.write(f"  {label} — {ts}\n")
    stream.write(f"  argv: {' '.join(argv) if argv else '(no args)'}\n")
    stream.write(f"{bar}\n")
    stream.flush()


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
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    raw_argv = argv if argv is not None else sys.argv[1:]

    log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    progress_streams: list[TextIO] = [sys.stdout]

    log_fp: TextIO | None = None
    err_fp: TextIO | None = None
    if not args.no_log_file:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_fp = LOG_FILE.open("a", encoding="utf-8")
        err_fp = ERR_FILE.open("a", encoding="utf-8")
        _write_header(log_fp, "daily_update", raw_argv)
        _write_header(err_fp, "daily_update", raw_argv)
        progress_streams.append(log_fp)
        log_handlers.append(logging.StreamHandler(err_fp))

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=log_handlers,
        force=True,
    )

    def progress(line: str) -> None:
        for s in progress_streams:
            s.write(line + "\n")
            s.flush()

    etf_symbols = [s.upper() for s in args.etfs] if args.etfs else None
    av = AlphaVantageClient(cache=args.cache)

    try:
        report = daily_svc.daily_update(
            etf_symbols=etf_symbols,
            client=av,
            progress=progress,
            skip_prices=args.skip_prices,
            skip_indicators=args.skip_indicators,
            skip_indexes=args.skip_indexes,
            limit_symbols=args.limit_symbols,
        )
    finally:
        if log_fp is not None:
            log_fp.close()
        if err_fp is not None:
            err_fp.close()

    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
