"""Admin tab.

Two tools in one tab:

- **Daily update** — the production path.  Incremental refresh of one
  ETF or every tracked ETF.  Symbol-union deduplication ensures
  overlap between ETFs (AAPL in SPY + QQQ + XLK) costs exactly one
  price call per cycle.
- **Full refresh (test protocol)** — a single-ETF, delete-all-and-
  reload bootstrap.  Used to seed a new ETF for the first time or
  to recover from corruption.  Supports a ``--limit`` knob for quick
  iteration.

Both run on a ``QThread`` so the UI stays live; progress streams into
the shared log pane at the bottom.  Poller controls, risk-rule
editor, and source-health detail arrive in later phases.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market_analysis.app.widgets.ingest_worker import DailyWorker, FullRefreshWorker
from market_analysis.services import queries
from market_analysis.services.config import get_settings
from market_analysis.services.ingestors import daily as daily_svc


ALL_ETFS_LABEL = "— All tracked ETFs —"


class AdminTab(QWidget):
    """Operational admin surface."""

    #: Emitted when any ingest run completes (success flag, summary).
    ingest_finished = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker = None

        root = QHBoxLayout(self)
        root.addWidget(self._build_left(), 1)
        root.addWidget(self._build_right(), 2)

        self.refresh()

    # -- Layout --------------------------------------------------------

    def _build_left(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        # Health group
        health = QGroupBox("Mongo")
        hl = QFormLayout(health)
        self._uri_lbl = QLabel("-")
        self._db_lbl = QLabel("-")
        self._status_lbl = QLabel("-")
        self._schema_lbl = QLabel("-")
        hl.addRow("URI:", self._uri_lbl)
        hl.addRow("Database:", self._db_lbl)
        hl.addRow("Status:", self._status_lbl)
        hl.addRow("Schema version:", self._schema_lbl)
        layout.addWidget(health)

        # Credentials group
        creds = QGroupBox("Credentials")
        cl = QFormLayout(creds)
        self._av_lbl = QLabel("-")
        self._sch_lbl = QLabel("-")
        cl.addRow("Alpha Vantage:", self._av_lbl)
        cl.addRow("Schwab:", self._sch_lbl)
        layout.addWidget(creds)

        # Collection counts
        counts = QGroupBox("Collections")
        cg = QVBoxLayout(counts)
        self._counts = QTableWidget(0, 2)
        self._counts.setHorizontalHeaderLabels(["Collection", "Docs"])
        self._counts.verticalHeader().setVisible(False)
        self._counts.horizontalHeader().setStretchLastSection(True)
        self._counts.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        cg.addWidget(self._counts)
        layout.addWidget(counts, 1)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)

        return box

    def _build_right(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        # ---- Daily update (production) ----
        daily = QGroupBox("Daily update")
        dg = QFormLayout(daily)

        self._daily_scope = QComboBox()
        self._daily_scope.addItem(ALL_ETFS_LABEL)
        # Populated lazily in refresh().

        self._daily_skip_prices = QCheckBox("Skip prices")
        self._daily_skip_indicators = QCheckBox("Skip indicators")
        self._daily_cache = QCheckBox("Use disk cache")
        opts = QHBoxLayout()
        opts.addWidget(self._daily_skip_prices)
        opts.addWidget(self._daily_skip_indicators)
        opts.addWidget(self._daily_cache)
        opts_w = QWidget(); opts_w.setLayout(opts)

        self._daily_run_btn = QPushButton("Run daily update")
        self._daily_run_btn.clicked.connect(self._on_daily_clicked)

        dg.addRow("Scope:", self._daily_scope)
        dg.addRow("Options:", opts_w)
        dg.addRow("", self._daily_run_btn)
        layout.addWidget(daily)

        # ---- Full refresh (test protocol) ----
        full = QGroupBox("Full refresh (test / initial load)")
        fg = QFormLayout(full)

        self._full_symbol = QLineEdit("SPY")
        self._full_symbol.setPlaceholderText("ETF ticker (e.g. SPY)")

        self._full_limit = QSpinBox()
        self._full_limit.setRange(0, 10_000)
        self._full_limit.setSpecialValueText("all")
        self._full_limit.setValue(3)

        self._full_skip_prices = QCheckBox("Skip prices")
        self._full_skip_indicators = QCheckBox("Skip indicators")
        self._full_cache = QCheckBox("Use disk cache")
        self._full_cache.setChecked(True)
        opts2 = QHBoxLayout()
        opts2.addWidget(self._full_skip_prices)
        opts2.addWidget(self._full_skip_indicators)
        opts2.addWidget(self._full_cache)
        opts2_w = QWidget(); opts2_w.setLayout(opts2)

        self._full_run_btn = QPushButton("Run full refresh")
        self._full_run_btn.clicked.connect(self._on_full_clicked)

        fg.addRow("Symbol:", self._full_symbol)
        fg.addRow("Limit holdings:", self._full_limit)
        fg.addRow("Options:", opts2_w)
        fg.addRow("", self._full_run_btn)
        layout.addWidget(full)

        # ---- Log ----
        log_box = QGroupBox("Log")
        ll = QVBoxLayout(log_box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Menlo", 10))
        self._log.setMaximumBlockCount(10_000)
        ll.addWidget(self._log)
        layout.addWidget(log_box, 1)

        return box

    # -- Slots ---------------------------------------------------------

    def refresh(self) -> None:
        """Repopulate all read-only panels and the ETF selector."""
        s = get_settings()
        self._uri_lbl.setText(s.mongo.uri)
        self._db_lbl.setText(s.mongo.database)
        self._av_lbl.setText("configured" if s.has_alpha_vantage_key() else "not configured")
        self._sch_lbl.setText("configured" if s.has_schwab_credentials() else "not configured")

        h = queries.db_health()
        self._status_lbl.setText("reachable" if h.reachable else "UNREACHABLE")
        self._schema_lbl.setText(
            "—" if h.schema_version is None else f"v{h.schema_version}"
        )
        self._counts.setRowCount(len(h.counts))
        for i, (name, n) in enumerate(sorted(h.counts.items())):
            self._counts.setItem(i, 0, QTableWidgetItem(name))
            item = QTableWidgetItem(f"{n:,}" if n >= 0 else "error")
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._counts.setItem(i, 1, item)
        self._counts.resizeColumnsToContents()

        # Refresh ETF scope dropdown without clobbering current selection.
        prev = self._daily_scope.currentText()
        etfs = daily_svc.tracked_etfs() if h.reachable else []
        self._daily_scope.blockSignals(True)
        self._daily_scope.clear()
        self._daily_scope.addItem(ALL_ETFS_LABEL)
        for s in etfs:
            self._daily_scope.addItem(s)
        if prev:
            idx = self._daily_scope.findText(prev)
            if idx >= 0:
                self._daily_scope.setCurrentIndex(idx)
        self._daily_scope.blockSignals(False)

    # -- Run buttons ---------------------------------------------------

    def _busy(self) -> bool:
        return self._thread is not None

    def _on_daily_clicked(self) -> None:
        if self._busy():
            return
        scope = self._daily_scope.currentText()
        etf_symbols = None if scope == ALL_ETFS_LABEL else [scope]

        self._log.clear()
        self._log.appendPlainText(
            f"Daily update — scope: {'all tracked ETFs' if etf_symbols is None else scope}"
        )
        self._set_buttons_enabled(False)

        self._thread = QThread(self)
        self._worker = DailyWorker(
            etf_symbols=etf_symbols,
            skip_prices=self._daily_skip_prices.isChecked(),
            skip_indicators=self._daily_skip_indicators.isChecked(),
            cache=self._daily_cache.isChecked(),
        )
        self._wire_thread()

    def _on_full_clicked(self) -> None:
        if self._busy():
            return
        symbol = self._full_symbol.text().strip().upper()
        if not symbol:
            self._log.appendPlainText("ERROR: enter a symbol.")
            return

        lim_val = self._full_limit.value()
        limit = None if lim_val == 0 else lim_val

        self._log.clear()
        self._log.appendPlainText(
            f"Full refresh — {symbol} (limit={limit or 'all'})"
        )
        self._set_buttons_enabled(False)

        self._thread = QThread(self)
        self._worker = FullRefreshWorker(
            symbol,
            limit=limit,
            skip_prices=self._full_skip_prices.isChecked(),
            skip_indicators=self._full_skip_indicators.isChecked(),
            cache=self._full_cache.isChecked(),
        )
        self._wire_thread()

    def _wire_thread(self) -> None:
        assert self._worker is not None and self._thread is not None
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log.appendPlainText)
        self._worker.done.connect(self._on_ingest_done)
        self._thread.start()

    def _on_ingest_done(self, success: bool, summary: str) -> None:
        self._log.appendPlainText(
            f"{'DONE' if success else 'FAILED'}: {summary}"
        )
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        self._set_buttons_enabled(True)
        self.refresh()
        self.ingest_finished.emit(success, summary)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        self._daily_run_btn.setEnabled(enabled)
        self._full_run_btn.setEnabled(enabled)
