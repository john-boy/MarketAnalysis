"""Daily update orchestrator.

Drives a cost-efficient incremental refresh across one ETF or every
ETF tracked in the ``etf`` collection.  The key design choice is
**symbol-union deduplication**: overlapping holdings (AAPL is in SPY
and QQQ and XLK) incur exactly one price call and one indicator
recompute per cycle, not one per ETF.

Pipeline:

1. **ETF profile refresh.**  One call per ETF (`ETF_PROFILE`).  Cheap,
   and catches holding/weight changes.
2. **Union holdings** across every refreshed ETF into a sorted unique
   symbol set.
3. **Incremental prices.**  `ingest_prices(symbol, mode='auto')` — AV
   ``compact`` window, filtered to ``date > last_stored``.
4. **Incremental indicators.**  `recompute_for_symbol(symbol,
   mode='auto')` — append-only for each ``(indicator, stack)``.

Callable from the Admin tab, the scheduled poller, and CLI scripts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from market_analysis.data import mongo
from market_analysis.services import indicators as ind_svc
from market_analysis.services import panel as panel_svc
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import indexes as index_ingestor
from market_analysis.services.ingestors import prices as price_ingestor
from market_analysis.services.ingestors._common import is_tradeable_symbol
from market_analysis.sources.alpha_vantage import AlphaVantageClient, AlphaVantageError


log = logging.getLogger(__name__)

# A ``Progress`` hook receives one human-readable line per step.
Progress = Callable[[str], None]


@dataclass
class DailyReport:
    etfs_refreshed: list[str] = field(default_factory=list)
    unique_symbols: int = 0
    prices_updated: int = 0            # symbols that gained >= 1 bar
    prices_up_to_date: int = 0         # symbols with no new bars
    prices_bootstrapped: int = 0       # symbols seeded from empty
    prices_escalated: int = 0          # gap > 100 days → full refresh
    prices_empty_payload: int = 0      # AV returned no rows (silent miss)
    indicators_updated: int = 0
    indexes_refreshed: list[str] = field(default_factory=list)
    index_errors: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Reconciliation
    holdings_dropped: int = 0          # total drops across refreshed ETFs
    memberships_pulled: int = 0        # company.etf_memberships entries removed
    orphaned_companies: int = 0        # companies with no etf_memberships
    elapsed_sec: float = 0.0


def tracked_etfs() -> list[str]:
    """Return sorted symbols of every ETF we have ingested at least once."""
    return sorted(mongo.etf().distinct("symbol"))


def daily_update(
    *,
    etf_symbols: list[str] | None = None,
    client: AlphaVantageClient | None = None,
    progress: Progress | None = None,
    skip_prices: bool = False,
    skip_indicators: bool = False,
    skip_indexes: bool = False,
    limit_symbols: int | None = None,
) -> DailyReport:
    """Run the daily pipeline.

    ``etf_symbols=None`` means "every ETF in the ``etf`` collection".
    Pass a list to narrow the scope (e.g. ``["SPY"]``).
    ``limit_symbols`` caps the union (for test runs); applied after
    dedup and sorting.
    """
    av = client or AlphaVantageClient()
    say: Progress = progress or (lambda line: None)
    report = DailyReport()
    t0 = time.monotonic()

    # 1. Resolve + refresh ETF profiles -------------------------------
    if etf_symbols is None:
        etf_symbols = tracked_etfs()
    etf_symbols = [s.upper() for s in etf_symbols]
    if not etf_symbols:
        say("No tracked ETFs; nothing to do.")
        report.elapsed_sec = time.monotonic() - t0
        return report

    say(f"[1/5] Refreshing {len(etf_symbols)} ETF profile(s)…")
    union: set[str] = set()
    for sym in etf_symbols:
        try:
            rep = etf_ingestor.ingest_etf(sym, client=av)
            union.update(rep.holding_symbols)
            report.etfs_refreshed.append(sym)
            report.holdings_dropped   += len(rep.dropped_symbols)
            report.memberships_pulled += rep.memberships_pulled
            msg = f"  {sym}: {rep.holdings_count} holdings"
            if rep.dropped_symbols:
                preview = ', '.join(rep.dropped_symbols[:5])
                more = '' if len(rep.dropped_symbols) <= 5 else f' (+{len(rep.dropped_symbols) - 5} more)'
                msg += (f"  dropped {len(rep.dropped_symbols)}: {preview}{more}"
                        f"  (memberships pulled: {rep.memberships_pulled})")
            say(msg)
        except AlphaVantageError as e:
            msg = f"{sym}: ETF profile error: {e}"
            say(f"  {msg}")
            report.errors.append(msg)
        except Exception as e:  # noqa: BLE001
            msg = f"{sym}: unexpected: {e}"
            log.exception("%s: unexpected ETF ingest error", sym)
            report.errors.append(msg)
            say(f"  {msg}")

    # 2. Union deduplication ------------------------------------------
    # Drop cash / N.A. placeholders before anything hits AV.  Also fold
    # the ETF symbols themselves into the panel — SPY/QQQ/etc. are
    # tradeable in their own right and users expect them charted.
    # Finally, fold in orphaned companies (no ETF membership) so their
    # price history continues to flow until the user explicitly deletes
    # them from the Company UI.
    union = {s for s in union if is_tradeable_symbol(s)}
    union.update(etf_symbols)
    orphans = [s for s in etf_ingestor.list_orphaned_companies() if is_tradeable_symbol(s)]
    if orphans:
        union.update(orphans)
        report.orphaned_companies = len(orphans)
        say(f"        + {len(orphans)} orphaned compan{'y' if len(orphans) == 1 else 'ies'} "
            f"(no ETF membership) folded into the panel.")
    symbols = sorted(union)
    if limit_symbols is not None:
        symbols = symbols[:limit_symbols]
    report.unique_symbols = len(symbols)
    say(f"[2/5] {len(symbols)} unique holding symbols after union.")

    # 3. Incremental prices -------------------------------------------
    if skip_prices:
        say("[3/5] Skipping prices.")
    else:
        say(f"[3/5] Updating prices (incremental)…")
        for i, sym in enumerate(symbols, 1):
            try:
                r = price_ingestor.ingest_prices(sym, client=av, mode="auto")
            except AlphaVantageError as e:
                msg = f"{sym}: price error: {e}"
                say(f"  [{i:>4}/{len(symbols)}] {msg}")
                report.errors.append(msg)
                continue
            except Exception as e:  # noqa: BLE001
                msg = f"{sym}: unexpected price error: {e}"
                log.exception("%s: unexpected price error", sym)
                report.errors.append(msg)
                say(f"  [{i:>4}/{len(symbols)}] {msg}")
                continue

            if r.escalated:
                report.prices_escalated += 1
            if r.mode == "full":
                report.prices_bootstrapped += 1
                say(f"  [{i:>4}/{len(symbols)}] {sym}: bootstrapped "
                    f"({r.inserted:,} bars)")
            elif r.inserted > 0:
                report.prices_updated += 1
                say(f"  [{i:>4}/{len(symbols)}] {sym}: "
                    f"+{r.inserted} bars (→ {r.last_date.date()})")
            elif r.empty_payload:
                # AV returned an empty Time Series — distinct from
                # "already up-to-date".  Surface it so callers can see
                # which symbols silently missed their refresh.
                report.prices_empty_payload += 1
                msg = f"{sym}: AV returned no rows (no update applied)"
                report.errors.append(msg)
                say(f"  [{i:>4}/{len(symbols)}] {msg}")
            else:
                report.prices_up_to_date += 1
                # Keep it quiet — one line per up-to-date symbol is noise.

    # 4. Index refresh (proxy stamp + direct-mode fetch) -------------
    direct_index_symbols: list[str] = []
    if skip_indexes:
        say("[4/5] Skipping indexes.")
    else:
        tracked = index_ingestor.list_tracked()
        if not tracked:
            say("[4/5] No tracked indexes.")
        else:
            say(f"[4/5] Refreshing {len(tracked)} index(es)…")
            for rec in tracked:
                try:
                    ir = index_ingestor.ingest_index(rec.symbol, client=av, mode="auto")
                except Exception as e:  # noqa: BLE001
                    msg = f"{rec.symbol}: index error: {e}"
                    log.exception("%s: unexpected index ingest error", rec.symbol)
                    report.index_errors.append(msg)
                    say(f"  {msg}")
                    continue
                if ir.error:
                    report.index_errors.append(f"{rec.symbol}: {ir.error}")
                    say(f"  {rec.symbol} ({ir.mode}): {ir.error}")
                    continue
                report.indexes_refreshed.append(rec.symbol)
                if ir.mode == "proxy":
                    say(f"  {rec.symbol}: proxy {ir.proxy_symbol} ok")
                else:
                    direct_index_symbols.append(rec.symbol)
                    if ir.inserted:
                        say(f"  {rec.symbol}: +{ir.inserted} bars "
                            f"(→ {ir.last_date.date() if ir.last_date else '—'})")
                    else:
                        say(f"  {rec.symbol}: up-to-date")

    # 5. Incremental indicators (batched across all symbols) ---------
    if skip_indicators:
        say("[5/5] Skipping indicators.")
    else:
        # Include direct-mode index symbols (e.g. VIX) so they get
        # their own RSI/EMA stacks.  Proxy-mode indexes piggyback on
        # their proxy ETF's computation.
        panel_symbols = sorted(set(symbols) | set(direct_index_symbols))
        say(f"[5/5] Updating indicators (batched over {len(panel_symbols)} symbols)…")
        try:
            r = panel_svc.recompute_panel(panel_symbols, mode="auto")
            # Roll up per-symbol insert totals across every (indicator, stack).
            per_symbol: dict[str, int] = {}
            for counts in r.counts.values():
                for sym, n in counts.items():
                    per_symbol[sym] = per_symbol.get(sym, 0) + n
            report.indicators_updated = sum(1 for n in per_symbol.values() if n > 0)
            total = sum(per_symbol.values())
            say(
                f"  panel: {r.symbols} symbols × {r.bars} bars → "
                f"{total:,} docs inserted across {report.indicators_updated} symbols"
            )
        except Exception as e:  # noqa: BLE001
            msg = f"batched indicators: {e}"
            log.exception("batched indicator recompute failed")
            report.errors.append(msg)
            say(f"  {msg}")

    report.elapsed_sec = time.monotonic() - t0
    say(
        f"Done in {report.elapsed_sec:.1f}s. "
        f"ETFs={len(report.etfs_refreshed)} "
        f"symbols={report.unique_symbols} "
        f"updated={report.prices_updated} "
        f"bootstrapped={report.prices_bootstrapped} "
        f"up-to-date={report.prices_up_to_date} "
        f"escalated={report.prices_escalated} "
        f"empty-payload={report.prices_empty_payload} "
        f"ind-updated={report.indicators_updated} "
        f"indexes={len(report.indexes_refreshed)} "
        f"dropped-holdings={report.holdings_dropped} "
        f"memberships-pulled={report.memberships_pulled} "
        f"orphans={report.orphaned_companies} "
        f"errors={len(report.errors) + len(report.index_errors)}"
    )
    return report
