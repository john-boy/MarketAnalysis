"""Admin tab — operational surface.

Four tools in a tabbed right pane, plus a collapsible log:

- **Daily update** — incremental refresh across one or every tracked
  ETF.  Now also refreshes every tracked index and includes direct-
  mode index symbols (e.g. VIX) in indicator computation.
- **Full refresh** — single-ETF delete-all-and-reload.  First-time
  seeding or corruption recovery.
- **ETFs** — list / add / remove / re-ingest tracked ETFs without
  leaving the tab.
- **Indexes** — same for market indexes (SPX, NDX, VIX, …).  Supports
  both proxy mode (SPX→SPY) and direct mode (VIX).

The log is a **collapsible** pane at the bottom — useful during long
ingests, otherwise folded away so the management tables get the
vertical real estate.
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
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from market_analysis.app.widgets.ingest_worker import (
    DailyWorker,
    ETFIngestWorker,
    FullRefreshWorker,
    IndexIngestWorker,
)
from market_analysis.data.models import MarketIndex
from market_analysis.services import queries
from market_analysis.services.config import get_settings
from market_analysis.services.ingestors import daily as daily_svc
from market_analysis.services.ingestors import etf as etf_ingestor
from market_analysis.services.ingestors import indexes as index_ingestor


ALL_ETFS_LABEL = "— All tracked ETFs —"


class AdminTab(QWidget):
    """Operational admin surface."""

    ingest_finished = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker = None

        root = QHBoxLayout(self)
        root.addWidget(self._build_left(), 1)
        root.addWidget(self._build_right(), 3)

        self.refresh()

    # -- Left pane: health + counts -----------------------------------

    def _build_left(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

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

        creds = QGroupBox("Credentials")
        cl = QFormLayout(creds)
        self._av_lbl = QLabel("-")
        self._sch_lbl = QLabel("-")
        cl.addRow("Alpha Vantage:", self._av_lbl)
        cl.addRow("Schwab:", self._sch_lbl)
        layout.addWidget(creds)

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

    # -- Right pane: tabs + collapsible log ---------------------------

    def _build_right(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_daily_tab(), "Daily")
        self._tabs.addTab(self._build_full_tab(), "Full refresh")
        self._tabs.addTab(self._build_etf_tab(), "ETFs")
        self._tabs.addTab(self._build_index_tab(), "Indexes")
        splitter.addWidget(self._tabs)

        splitter.addWidget(self._build_log_panel())

        # Log gets the lion's share of vertical space — the tab forms
        # are compact and don't expand to fill.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 600])

        return splitter

    def _build_log_panel(self) -> QWidget:
        """A checkable GroupBox acts as a cheap collapsible panel."""
        self._log_box = QGroupBox("Log")
        self._log_box.setCheckable(True)
        self._log_box.setChecked(True)  # expanded by default — fills the splitter
        ll = QVBoxLayout(self._log_box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Menlo", 10))
        self._log.setMaximumBlockCount(10_000)
        ll.addWidget(self._log)
        self._log_box.toggled.connect(self._log.setVisible)
        return self._log_box

    def _log_line(self, line: str) -> None:
        """Append to the log; auto-expand the panel so the user sees progress."""
        if not self._log_box.isChecked():
            self._log_box.setChecked(True)
        self._log.appendPlainText(line)

    # -- Tab: Daily ---------------------------------------------------

    def _build_daily_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

        self._daily_scope = QComboBox()
        self._daily_scope.addItem(ALL_ETFS_LABEL)

        self._daily_skip_prices = QCheckBox("Skip prices")
        self._daily_skip_indicators = QCheckBox("Skip indicators")
        self._daily_skip_indexes = QCheckBox("Skip indexes")
        self._daily_cache = QCheckBox("Use disk cache")
        opts = QHBoxLayout()
        opts.addWidget(self._daily_skip_prices)
        opts.addWidget(self._daily_skip_indicators)
        opts.addWidget(self._daily_skip_indexes)
        opts.addWidget(self._daily_cache)
        opts_w = QWidget(); opts_w.setLayout(opts)

        self._daily_run_btn = QPushButton("Run daily update")
        self._daily_run_btn.clicked.connect(self._on_daily_clicked)

        layout.addRow("Scope:", self._daily_scope)
        layout.addRow("Options:", opts_w)
        layout.addRow("", self._daily_run_btn)
        return w

    # -- Tab: Full refresh --------------------------------------------

    def _build_full_tab(self) -> QWidget:
        w = QWidget()
        layout = QFormLayout(w)

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

        layout.addRow("Symbol:", self._full_symbol)
        layout.addRow("Limit holdings:", self._full_limit)
        layout.addRow("Options:", opts2_w)
        layout.addRow("", self._full_run_btn)
        return w

    # -- Tab: ETFs ----------------------------------------------------

    def _build_etf_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._etf_table = QTableWidget(0, 2)
        self._etf_table.setHorizontalHeaderLabels(["Symbol", "Name"])
        self._etf_table.verticalHeader().setVisible(False)
        self._etf_table.horizontalHeader().setStretchLastSection(True)
        self._etf_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._etf_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._etf_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._etf_table, 1)

        # Add / ingest row
        add_row = QHBoxLayout()
        self._etf_add_input = QLineEdit()
        self._etf_add_input.setPlaceholderText("New ETF ticker (e.g. XLF)")
        add_btn = QPushButton("Add + ingest")
        add_btn.clicked.connect(self._on_etf_add_clicked)
        add_row.addWidget(QLabel("Add:"))
        add_row.addWidget(self._etf_add_input, 1)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)

        # Action row for the selected ETF.  Two ingest buttons:
        #   Ingest        → only pulls holdings with no existing data
        #   Re-ingest     → full delete-and-reload for every holding
        act = QHBoxLayout()
        ingest_btn = QPushButton("Ingest selected")
        ingest_btn.setToolTip("Only fetch missing holdings (fast).")
        ingest_btn.clicked.connect(self._on_etf_ingest_clicked)
        reingest_btn = QPushButton("Re-ingest selected")
        reingest_btn.setToolTip("Full reload of every holding (slow).")
        reingest_btn.clicked.connect(self._on_etf_reingest_clicked)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_etf_remove_clicked)
        act.addWidget(ingest_btn)
        act.addWidget(reingest_btn)
        act.addWidget(remove_btn)
        act.addStretch(1)
        layout.addLayout(act)

        self._etf_buttons = [add_btn, ingest_btn, reingest_btn, remove_btn]
        return w

    # -- Tab: Indexes -------------------------------------------------

    def _build_index_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        self._idx_table = QTableWidget(0, 5)
        self._idx_table.setHorizontalHeaderLabels(
            ["Symbol", "Name", "Mode", "Source", "Last ingested"]
        )
        self._idx_table.verticalHeader().setVisible(False)
        self._idx_table.horizontalHeader().setStretchLastSection(True)
        self._idx_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._idx_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        layout.addWidget(self._idx_table, 1)

        # Add row
        form = QFormLayout()
        self._idx_symbol = QLineEdit()
        self._idx_symbol.setPlaceholderText("Canonical symbol (e.g. RUT)")
        self._idx_name = QLineEdit()
        self._idx_name.setPlaceholderText("Human-readable name (optional)")
        self._idx_mode = QComboBox()
        self._idx_mode.addItems(["proxy (ETF stand-in)", "direct (own fetch)"])
        self._idx_source = QLineEdit()
        self._idx_source.setPlaceholderText("Proxy ETF symbol, or fetch ticker")
        form.addRow("Symbol:", self._idx_symbol)
        form.addRow("Name:", self._idx_name)
        form.addRow("Mode:", self._idx_mode)
        form.addRow("Source symbol:", self._idx_source)
        layout.addLayout(form)

        act = QHBoxLayout()
        add_btn = QPushButton("Add index")
        add_btn.clicked.connect(self._on_index_add_clicked)
        ingest_btn = QPushButton("Ingest selected")
        ingest_btn.setToolTip("Incremental fetch of new bars only.")
        ingest_btn.clicked.connect(self._on_index_ingest_clicked)
        reingest_btn = QPushButton("Re-ingest selected")
        reingest_btn.setToolTip("Full reload of the index's price history.")
        reingest_btn.clicked.connect(self._on_index_reingest_clicked)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_index_remove_clicked)
        act.addWidget(add_btn)
        act.addWidget(ingest_btn)
        act.addWidget(reingest_btn)
        act.addWidget(remove_btn)
        act.addStretch(1)
        layout.addLayout(act)

        self._idx_buttons = [add_btn, ingest_btn, reingest_btn, remove_btn]
        return w

    # -- Refresh ------------------------------------------------------

    def refresh(self) -> None:
        s = get_settings()
        self._uri_lbl.setText(s.mongo.uri)
        self._db_lbl.setText(s.mongo.database)
        self._av_lbl.setText(
            "configured" if s.has_alpha_vantage_key() else "not configured"
        )
        self._sch_lbl.setText(
            "configured" if s.has_schwab_credentials() else "not configured"
        )

        h = queries.db_health()
        self._status_lbl.setText("reachable" if h.reachable else "UNREACHABLE")
        self._schema_lbl.setText(
            "—" if h.schema_version is None else f"v{h.schema_version}"
        )
        self._counts.setRowCount(len(h.counts))
        for i, (name, n) in enumerate(sorted(h.counts.items())):
            self._counts.setItem(i, 0, QTableWidgetItem(name))
            item = QTableWidgetItem(f"{n:,}" if n >= 0 else "error")
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._counts.setItem(i, 1, item)
        self._counts.resizeColumnsToContents()

        self._refresh_daily_scope(h.reachable)
        if h.reachable:
            self._refresh_etf_table()
            self._refresh_index_table()

    def _refresh_daily_scope(self, reachable: bool) -> None:
        prev = self._daily_scope.currentText()
        etfs = daily_svc.tracked_etfs() if reachable else []
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

    def _refresh_etf_table(self) -> None:
        rows = []
        for sym in etf_ingestor.list_tracked_etfs():
            doc = queries.load_etf(sym) or {}
            rows.append((sym, doc.get("name") or ""))
        self._etf_table.setRowCount(len(rows))
        for i, (sym, name) in enumerate(rows):
            self._etf_table.setItem(i, 0, QTableWidgetItem(sym))
            self._etf_table.setItem(i, 1, QTableWidgetItem(name))

    def _refresh_index_table(self) -> None:
        rows = queries.list_indexes()
        self._idx_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._idx_table.setItem(i, 0, QTableWidgetItem(r["symbol"]))
            self._idx_table.setItem(i, 1, QTableWidgetItem(r.get("name") or ""))
            self._idx_table.setItem(i, 2, QTableWidgetItem(r["mode"]))
            self._idx_table.setItem(i, 3, QTableWidgetItem(r["price_source_symbol"]))
            last = r.get("last_ingested")
            last_s = last.strftime("%Y-%m-%d %H:%M") if last else "—"
            self._idx_table.setItem(i, 4, QTableWidgetItem(last_s))
        self._idx_table.resizeColumnsToContents()

    # -- Shared: run control ------------------------------------------

    def _busy(self) -> bool:
        return self._thread is not None

    def _wire_thread(self) -> None:
        assert self._worker is not None and self._thread is not None
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.log.connect(self._log_line)
        self._worker.done.connect(self._on_ingest_done)
        self._thread.start()

    def _on_ingest_done(self, success: bool, summary: str) -> None:
        self._log_line(f"{'DONE' if success else 'FAILED'}: {summary}")
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
        for b in self._etf_buttons:
            b.setEnabled(enabled)
        for b in self._idx_buttons:
            b.setEnabled(enabled)

    # -- Daily + Full refresh -----------------------------------------

    def _on_daily_clicked(self) -> None:
        if self._busy():
            return
        scope = self._daily_scope.currentText()
        etf_symbols = None if scope == ALL_ETFS_LABEL else [scope]

        self._log.clear()
        self._log_line(
            f"Daily update — scope: "
            f"{'all tracked ETFs' if etf_symbols is None else scope}"
        )
        self._set_buttons_enabled(False)
        self._thread = QThread(self)
        self._worker = DailyWorker(
            etf_symbols=etf_symbols,
            skip_prices=self._daily_skip_prices.isChecked(),
            skip_indicators=self._daily_skip_indicators.isChecked(),
            skip_indexes=self._daily_skip_indexes.isChecked(),
            cache=self._daily_cache.isChecked(),
        )
        self._wire_thread()

    def _on_full_clicked(self) -> None:
        if self._busy():
            return
        symbol = self._full_symbol.text().strip().upper()
        if not symbol:
            self._log_line("ERROR: enter a symbol.")
            return
        lim_val = self._full_limit.value()
        limit = None if lim_val == 0 else lim_val

        self._log.clear()
        self._log_line(f"Full refresh — {symbol} (limit={limit or 'all'})")
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

    # -- ETF actions --------------------------------------------------

    def _selected_etf(self) -> str | None:
        row = self._etf_table.currentRow()
        if row < 0:
            return None
        item = self._etf_table.item(row, 0)
        return item.text() if item else None

    def _on_etf_add_clicked(self) -> None:
        if self._busy():
            return
        sym = self._etf_add_input.text().strip().upper()
        if not sym:
            self._log_line("ERROR: enter an ETF ticker.")
            return
        self._etf_add_input.clear()
        # Adding a new ETF: don't re-pull already-ingested holdings.
        self._run_etf_ingest(sym, limit=None, reingest=False)

    def _on_etf_ingest_clicked(self) -> None:
        if self._busy():
            return
        sym = self._selected_etf()
        if not sym:
            return
        self._run_etf_ingest(sym, limit=None, reingest=False)

    def _on_etf_reingest_clicked(self) -> None:
        if self._busy():
            return
        sym = self._selected_etf()
        if not sym:
            return
        self._run_etf_ingest(sym, limit=None, reingest=True)

    def _run_etf_ingest(
        self, symbol: str, *, limit: int | None, reingest: bool,
    ) -> None:
        self._log.clear()
        self._log_line(
            f"ETF {'re-ingest' if reingest else 'ingest'} — {symbol}"
        )
        self._set_buttons_enabled(False)
        self._thread = QThread(self)
        self._worker = ETFIngestWorker(
            symbol,
            limit=limit,
            skip_prices=False,
            skip_indicators=False,
            cache=True,
            reingest=reingest,
        )
        self._wire_thread()

    def _on_etf_remove_clicked(self) -> None:
        if self._busy():
            return
        sym = self._selected_etf()
        if not sym:
            return
        resp = QMessageBox.question(
            self,
            "Remove ETF",
            f"Remove {sym} from the tracked ETF list?\n\n"
            "Holdings' price history is preserved (shared across ETFs). "
            "ETF membership on each company will be trimmed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        ok = etf_ingestor.remove_etf(sym)
        self._log_line(f"Removed ETF {sym}: {ok}")
        self.refresh()

    # -- Index actions ------------------------------------------------

    def _selected_index(self) -> str | None:
        row = self._idx_table.currentRow()
        if row < 0:
            return None
        item = self._idx_table.item(row, 0)
        return item.text() if item else None

    def _on_index_add_clicked(self) -> None:
        if self._busy():
            return
        sym = self._idx_symbol.text().strip().upper()
        src = self._idx_source.text().strip().upper()
        if not sym or not src:
            self._log_line("ERROR: index needs both symbol and source symbol.")
            return
        is_proxy = self._idx_mode.currentIndex() == 0
        try:
            rec = MarketIndex(
                symbol=sym,
                name=self._idx_name.text().strip() or None,
                proxy_symbol=src if is_proxy else None,
                fetch_symbol=None if is_proxy else src,
            )
        except ValueError as e:
            self._log_line(f"ERROR: {e}")
            return

        inserted = index_ingestor.add_index(rec)
        if inserted:
            self._log_line(f"Added index {sym} ({rec.mode} → {src}).")
        else:
            self._log_line(f"Index {sym} already exists.")
        self._idx_symbol.clear()
        self._idx_name.clear()
        self._idx_source.clear()
        self.refresh()

    def _on_index_ingest_clicked(self) -> None:
        self._run_index_ingest(reingest=False)

    def _on_index_reingest_clicked(self) -> None:
        self._run_index_ingest(reingest=True)

    def _run_index_ingest(self, *, reingest: bool) -> None:
        if self._busy():
            return
        sym = self._selected_index()
        if not sym:
            return
        self._log.clear()
        self._log_line(
            f"Index {'re-ingest' if reingest else 'ingest'} — {sym}"
        )
        self._set_buttons_enabled(False)
        self._thread = QThread(self)
        self._worker = IndexIngestWorker(sym, cache=True, reingest=reingest)
        self._wire_thread()

    def _on_index_remove_clicked(self) -> None:
        if self._busy():
            return
        sym = self._selected_index()
        if not sym:
            return
        resp = QMessageBox.question(
            self,
            "Remove index",
            f"Remove {sym} from the tracked index list?\n\n"
            "Direct-mode price data under this symbol is preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        ok = index_ingestor.remove_index(sym)
        self._log_line(f"Removed index {sym}: {ok}")
        self.refresh()
