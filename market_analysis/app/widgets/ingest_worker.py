"""Background workers for ingest runs.

Two workers:

- :class:`DailyWorker` runs the daily incremental pipeline
  (``services.ingestors.daily.daily_update``) across one ETF or every
  tracked ETF, with symbol-union deduplication.
- :class:`FullRefreshWorker` runs a single-ETF full refresh — used for
  initial loading and for the test protocol in the Admin tab.

Both emit ``log(str)`` per step and ``done(bool, str)`` on completion.
"""

from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, Signal

from market_analysis.data import mongo
from market_analysis.services import indicators as ind_svc
from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import prices as price_ingestor
from market_analysis.services.ingestors._common import is_tradeable_symbol
from market_analysis.services.run_logging import run_log
from market_analysis.sources.alpha_vantage import AlphaVantageClient, AlphaVantageError


class _BaseWorker(QObject):
    log = Signal(str)
    done = Signal(bool, str)

    def run(self) -> None:
        try:
            self._run_inner()
        except Exception as exc:  # noqa: BLE001
            self.log.emit(f"FATAL: {exc}")
            self.log.emit(traceback.format_exc())
            self.done.emit(False, str(exc))

    def _run_inner(self) -> None:  # pragma: no cover
        raise NotImplementedError


class DailyWorker(_BaseWorker):
    """Run the daily incremental pipeline on a GUI thread."""

    def __init__(
        self,
        *,
        etf_symbols: list[str] | None,
        skip_prices: bool = False,
        skip_indicators: bool = False,
        skip_indexes: bool = False,
        limit_symbols: int | None = None,
        cache: bool = False,
    ) -> None:
        super().__init__()
        self._etf_symbols = etf_symbols
        self._skip_prices = skip_prices
        self._skip_indicators = skip_indicators
        self._skip_indexes = skip_indexes
        self._limit_symbols = limit_symbols
        self._cache = cache

    def _run_inner(self) -> None:
        av = AlphaVantageClient(cache=self._cache)
        scope = (
            "all" if self._etf_symbols is None
            else ",".join(self._etf_symbols)
        )
        with run_log(
            "daily_update",
            source=f"gui (scope={scope})",
            extra_progress=self.log.emit,
        ) as progress:
            report = daily_svc.daily_update(
                etf_symbols=self._etf_symbols,
                client=av,
                progress=progress,
                skip_prices=self._skip_prices,
                skip_indicators=self._skip_indicators,
                skip_indexes=self._skip_indexes,
                limit_symbols=self._limit_symbols,
            )
        summary = (
            f"{len(report.etfs_refreshed)} ETF(s), "
            f"{report.unique_symbols} unique symbols, "
            f"+prices: {report.prices_updated} updated / "
            f"{report.prices_bootstrapped} bootstrapped / "
            f"{report.prices_up_to_date} up-to-date, "
            f"indexes: {len(report.indexes_refreshed)} refreshed, "
            f"indicators updated: {report.indicators_updated}, "
            f"errors: {len(report.errors) + len(report.index_errors)}"
        )
        total_errors = len(report.errors) + len(report.index_errors)
        self.done.emit(total_errors == 0, summary)


def _has_price_history(symbol: str) -> bool:
    """True when ``price_history`` already holds at least one row for ``symbol``."""
    return (
        mongo.price_history().find_one(
            {"metadata.symbol": symbol}, projection={"_id": 1}
        )
        is not None
    )


class FullRefreshWorker(_BaseWorker):
    """Run a one-ETF refresh — two modes.

    - ``reingest=True`` (default): full delete-and-reload of every
      holding's price history and a full indicator recompute.  Use for
      corruption recovery or a deliberate rebuild.
    - ``reingest=False``: *incremental* ingest — skip holdings that
      already have price data, pull only the missing ones in ``auto``
      mode.  Faster and friendlier to rate limits when adding a new ETF
      whose holdings largely overlap with existing ones.

    Either mode also ingests the ETF ticker itself (SPY/QQQ/…) — each
    ETF is tradeable on its own and users expect it charted.
    """

    def __init__(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        skip_prices: bool = False,
        skip_indicators: bool = False,
        cache: bool = False,
        reingest: bool = True,
    ) -> None:
        super().__init__()
        self._symbol = symbol.upper()
        self._limit = limit
        self._skip_prices = skip_prices
        self._skip_indicators = skip_indicators
        self._cache = cache
        self._reingest = reingest

    def _run_inner(self) -> None:
        av = AlphaVantageClient(cache=self._cache)
        sym = self._symbol
        label = "Re-ingest" if self._reingest else "Ingest (missing only)"
        price_mode = "full" if self._reingest else "auto"
        ind_mode = "full" if self._reingest else "auto"

        self.log.emit(f"[{label}] [1/3] Fetching ETF profile for {sym}…")
        try:
            rep = etf_ingestor.ingest_etf(sym, client=av)
        except AlphaVantageError as e:
            self.log.emit(f"ERROR: {e}")
            self.done.emit(False, str(e))
            return
        self.log.emit(
            f"  {sym}: {rep.holdings_count} holdings, "
            f"{rep.companies_upserted} companies upserted"
        )

        # Build the price-target list.  The ETF ticker itself is always
        # included (it's tradeable); holdings are filtered by mode.
        holdings = [s for s in rep.holding_symbols if is_tradeable_symbol(s)]
        if self._reingest:
            holding_targets = holdings
        else:
            holding_targets = [s for s in holdings if not _has_price_history(s)]
            skipped = len(holdings) - len(holding_targets)
            if skipped:
                self.log.emit(
                    f"  skipping {skipped} holding(s) that already have price history"
                )
        targets = [sym] + [s for s in holding_targets if s != sym]
        if self._limit is not None:
            targets = targets[: self._limit]

        if self._skip_prices:
            self.log.emit("[2/3] Skipping prices.")
        else:
            self.log.emit(
                f"[2/3] Prices ({price_mode}) for {len(targets)} symbol(s)…"
            )
            for i, s in enumerate(targets, 1):
                try:
                    r = price_ingestor.ingest_prices(s, client=av, mode=price_mode)
                    first = r.first_date.date() if r.first_date else "-"
                    last = r.last_date.date() if r.last_date else "-"
                    self.log.emit(
                        f"  [{i:>3}/{len(targets)}] {s}: "
                        f"+{r.inserted:,} docs ({first} → {last})"
                    )
                except AlphaVantageError as e:
                    self.log.emit(f"  [{i:>3}/{len(targets)}] {s}: AV error: {e}")
                except Exception as e:  # noqa: BLE001
                    self.log.emit(f"  [{i:>3}/{len(targets)}] {s}: {e}")

        if self._skip_indicators:
            self.log.emit("[3/3] Skipping indicators.")
        else:
            self.log.emit(
                f"[3/3] Indicators ({ind_mode}) for {len(targets)} symbol(s)…"
            )
            for i, s in enumerate(targets, 1):
                try:
                    r = ind_svc.recompute_for_symbol(s, mode=ind_mode)
                    counts = ", ".join(f"{k}={v}" for k, v in r.counts.items())
                    self.log.emit(
                        f"  [{i:>3}/{len(targets)}] {s}: "
                        f"{r.quotes_read:,} quotes → {counts}"
                    )
                except Exception as e:  # noqa: BLE001
                    self.log.emit(f"  [{i:>3}/{len(targets)}] {s}: {e}")

        self.done.emit(True, f"{label}: {sym} — {len(targets)} symbols.")


class ETFIngestWorker(_BaseWorker):
    """Fetch ETF profile + full-refresh its holdings' prices & indicators.

    Convenience wrapper: equivalent to ``FullRefreshWorker`` but named
    for the ETF-management UI.  Delegates straight through.
    """

    def __init__(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        skip_prices: bool = False,
        skip_indicators: bool = False,
        cache: bool = False,
        reingest: bool = True,
    ) -> None:
        super().__init__()
        self._inner = FullRefreshWorker(
            symbol,
            limit=limit,
            skip_prices=skip_prices,
            skip_indicators=skip_indicators,
            cache=cache,
            reingest=reingest,
        )
        self._inner.log.connect(self.log)
        self._inner.done.connect(self.done)

    def _run_inner(self) -> None:
        self._inner.run()


class FundamentalsWorker(_BaseWorker):
    """Fetch + upsert Alpha Vantage OVERVIEW fundamentals for one symbol."""

    def __init__(self, symbol: str, *, cache: bool = False) -> None:
        super().__init__()
        self._symbol = symbol.upper()
        self._cache = cache

    def _run_inner(self) -> None:
        from market_analysis.services.ingestors import fundamentals as fx

        av = AlphaVantageClient(cache=self._cache)
        self.log.emit(f"Fetching fundamentals for {self._symbol}…")
        r = fx.ingest_fundamentals(self._symbol, client=av)
        if r.error:
            self.log.emit(f"  ERROR: {r.error}")
            self.done.emit(False, r.error)
            return
        if r.skipped_reason:
            self.log.emit(f"  skipped: {r.skipped_reason}")
            self.done.emit(False, r.skipped_reason)
            return
        self.log.emit(f"  {r.symbol}: {r.fields_seen} fields written.")
        self.done.emit(True, f"{r.symbol}: {r.fields_seen} fields")


class BatchFundamentalsWorker(_BaseWorker):
    """Refresh AV OVERVIEW fundamentals across many symbols.

    ``symbols=None`` means "every company in the ``companies`` collection".
    Per-symbol AV errors are recorded but never abort the loop.
    """

    def __init__(
        self,
        *,
        symbols: list[str] | None = None,
        cache: bool = False,
        limit: int | None = None,
    ) -> None:
        super().__init__()
        self._symbols = symbols
        self._cache = cache
        self._limit = limit

    def _run_inner(self) -> None:
        from market_analysis.services.ingestors import fundamentals as fx

        av = AlphaVantageClient(cache=self._cache)
        report = fx.ingest_fundamentals_batch(
            self._symbols,
            client=av,
            progress=self.log.emit,
            limit=self._limit,
        )
        summary = (
            f"updated {len(report.updated)} / requested {report.requested} "
            f"(skipped {len(report.skipped)}, errors {len(report.errors)})"
        )
        self.done.emit(len(report.errors) == 0, summary)


class IndexIngestWorker(_BaseWorker):
    """Ingest a single tracked index.

    ``reingest=True`` forces a full reload (delete + refetch); the
    default ``False`` runs the incremental (``auto``) path — matching
    the ETF worker's two-level semantics.
    """

    def __init__(
        self,
        symbol: str,
        *,
        cache: bool = False,
        reingest: bool = False,
    ) -> None:
        super().__init__()
        self._symbol = symbol.upper()
        self._cache = cache
        self._reingest = reingest

    def _run_inner(self) -> None:
        from market_analysis.services.ingestors import indexes as ix

        av = AlphaVantageClient(cache=self._cache)
        mode = "full" if self._reingest else "auto"
        label = "Re-ingest" if self._reingest else "Ingest"
        self.log.emit(f"{label} index {self._symbol} ({mode})…")
        try:
            r = ix.ingest_index(self._symbol, client=av, mode=mode)
        except KeyError as e:
            self.log.emit(f"ERROR: {e}")
            self.done.emit(False, str(e))
            return
        if r.error:
            self.log.emit(f"  {r.symbol}: {r.error}")
            self.done.emit(False, r.error)
            return
        if r.mode == "proxy":
            self.log.emit(
                f"  {r.symbol}: proxy {r.proxy_symbol} — "
                f"last bar {r.proxy_last_date.date() if r.proxy_last_date else '—'}"
            )
            summary = f"{r.symbol}: proxy {r.proxy_symbol} ok"
        else:
            self.log.emit(
                f"  {r.symbol}: +{r.inserted} bars "
                f"(→ {r.last_date.date() if r.last_date else '—'})"
            )
            summary = f"{r.symbol}: {r.inserted} new bars"
        self.done.emit(True, summary)


# Back-compat alias for any old callers; prefer the explicit names above.
IngestWorker = FullRefreshWorker
