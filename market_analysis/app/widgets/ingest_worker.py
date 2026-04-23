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

from market_analysis.services import indicators as ind_svc
from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import prices as price_ingestor
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
        limit_symbols: int | None = None,
        cache: bool = False,
    ) -> None:
        super().__init__()
        self._etf_symbols = etf_symbols
        self._skip_prices = skip_prices
        self._skip_indicators = skip_indicators
        self._limit_symbols = limit_symbols
        self._cache = cache

    def _run_inner(self) -> None:
        av = AlphaVantageClient(cache=self._cache)
        report = daily_svc.daily_update(
            etf_symbols=self._etf_symbols,
            client=av,
            progress=self.log.emit,
            skip_prices=self._skip_prices,
            skip_indicators=self._skip_indicators,
            limit_symbols=self._limit_symbols,
        )
        summary = (
            f"{len(report.etfs_refreshed)} ETF(s), "
            f"{report.unique_symbols} unique symbols, "
            f"+prices: {report.prices_updated} updated / "
            f"{report.prices_bootstrapped} bootstrapped / "
            f"{report.prices_up_to_date} up-to-date, "
            f"indicators updated: {report.indicators_updated}, "
            f"errors: {len(report.errors)}"
        )
        self.done.emit(len(report.errors) == 0, summary)


class FullRefreshWorker(_BaseWorker):
    """Run a one-ETF full refresh (initial load / repair)."""

    def __init__(
        self,
        symbol: str,
        *,
        limit: int | None = None,
        skip_prices: bool = False,
        skip_indicators: bool = False,
        cache: bool = False,
    ) -> None:
        super().__init__()
        self._symbol = symbol.upper()
        self._limit = limit
        self._skip_prices = skip_prices
        self._skip_indicators = skip_indicators
        self._cache = cache

    def _run_inner(self) -> None:
        av = AlphaVantageClient(cache=self._cache)
        sym = self._symbol

        self.log.emit(f"[1/3] Fetching ETF profile for {sym}…")
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

        targets = list(rep.holding_symbols)
        if self._limit is not None:
            targets = targets[: self._limit]

        if self._skip_prices:
            self.log.emit("[2/3] Skipping prices.")
        else:
            self.log.emit(f"[2/3] Full refresh of prices for {len(targets)} symbols…")
            for i, s in enumerate(targets, 1):
                try:
                    r = price_ingestor.ingest_prices(s, client=av, mode="full")
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
            self.log.emit(f"[3/3] Full recompute of indicators for {len(targets)} symbols…")
            for i, s in enumerate(targets, 1):
                try:
                    r = ind_svc.recompute_for_symbol(s, mode="full")
                    counts = ", ".join(f"{k}={v}" for k, v in r.counts.items())
                    self.log.emit(
                        f"  [{i:>3}/{len(targets)}] {s}: "
                        f"{r.quotes_read:,} quotes → {counts}"
                    )
                except Exception as e:  # noqa: BLE001
                    self.log.emit(f"  [{i:>3}/{len(targets)}] {s}: {e}")

        self.done.emit(True, f"Full refresh: {sym} — {len(targets)} symbols.")


# Back-compat alias for any old callers; prefer the explicit names above.
IngestWorker = FullRefreshWorker
