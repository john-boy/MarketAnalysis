"""CLI: ingest an ETF end-to-end.

Usage::

    python -m scripts.ingest_etf SPY
    python -m scripts.ingest_etf SPY --limit 10 --skip-indicators
    python -m scripts.ingest_etf SPY --include-etf-prices

Pipeline:

1. ``ingest_etf(symbol)`` → ``etf`` + minimal ``companies``.
2. For each holding (optionally capped by ``--limit``): ``ingest_prices``.
3. For each holding: ``recompute_for_symbol`` (EMA + RSI).

By default the ETF's own price series is **not** ingested (Phase 1
charts start at the holding level).  Pass ``--include-etf-prices`` to
also ingest and index the ETF symbol itself.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from market_analysis.services import indicators as ind_svc
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import prices as price_ingestor
from market_analysis.sources.alpha_vantage import AlphaVantageClient, AlphaVantageError


log = logging.getLogger("ingest_etf")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbol", help="ETF ticker, e.g. SPY")
    ap.add_argument("--limit", type=int, default=None,
                    help="Ingest at most N holdings (default: all).")
    ap.add_argument("--skip-prices", action="store_true",
                    help="Skip price ingest; only update ETF + companies.")
    ap.add_argument("--skip-indicators", action="store_true",
                    help="Skip indicator recompute.")
    ap.add_argument("--include-etf-prices", action="store_true",
                    help="Also ingest the ETF's own OHLCV series.")
    ap.add_argument("--cache", action="store_true",
                    help="Enable on-disk AV response cache (dev replays).")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    av = AlphaVantageClient(cache=args.cache)
    symbol = args.symbol.upper()
    t0 = time.monotonic()

    # 1. ETF profile ------------------------------------------------------
    print(f"[1/3] Fetching ETF profile for {symbol}...")
    try:
        etf_report = etf_ingestor.ingest_etf(symbol, client=av)
    except AlphaVantageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(f"  {symbol}: {etf_report.holdings_count} holdings, "
          f"{etf_report.companies_upserted} companies upserted")

    targets: list[str] = list(etf_report.holding_symbols)
    if args.include_etf_prices:
        targets.insert(0, symbol)
    if args.limit is not None:
        targets = targets[: args.limit]

    # 2. Prices -----------------------------------------------------------
    if args.skip_prices:
        print("[2/3] Skipping price ingest (--skip-prices).")
    else:
        print(f"[2/3] Ingesting prices for {len(targets)} symbols...")
        for i, sym in enumerate(targets, 1):
            try:
                r = price_ingestor.ingest_prices(sym, client=av, mode="full")
                print(f"  [{i:>3}/{len(targets)}] {sym}: "
                      f"+{r.inserted:,} docs "
                      f"({r.first_date.date() if r.first_date else '-'} "
                      f"→ {r.last_date.date() if r.last_date else '-'})")
            except AlphaVantageError as e:
                log.warning("%s: %s", sym, e)
            except Exception as e:  # noqa: BLE001
                log.exception("%s: unexpected ingest error: %s", sym, e)

    # 3. Indicators -------------------------------------------------------
    if args.skip_indicators:
        print("[3/3] Skipping indicator recompute (--skip-indicators).")
    else:
        print(f"[3/3] Recomputing indicators for {len(targets)} symbols...")
        for i, sym in enumerate(targets, 1):
            try:
                r = ind_svc.recompute_for_symbol(sym, mode="full")
                counts = ", ".join(f"{k}={v}" for k, v in r.counts.items())
                print(f"  [{i:>3}/{len(targets)}] {sym}: "
                      f"{r.quotes_read:,} quotes → {counts}")
            except Exception as e:  # noqa: BLE001
                log.exception("%s: indicator recompute failed: %s", sym, e)

    print(f"Done in {time.monotonic() - t0:.1f}s.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
