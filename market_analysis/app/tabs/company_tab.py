"""Company tab — single-ticker detail view.

Left: filterable list of symbols present in ``price_history``.
Right: a stacked chart with a price panel (adjusted close + three
EMA overlays) and a linked RSI panel below it (0-100, 30/70 lines);
a visibility toggle row above the chart controls which traces are
drawn.  Below the chart, a scrollable **Fundamentals** display
breaks the AV ``OVERVIEW`` payload into sectioned panels (Identity,
Valuation, Profitability, Dividend, Trading, Analyst) with a
"Fetch fundamentals" button that ingests on demand.

Filings and news panels arrive in later phases.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from market_analysis.app.widgets.ingest_worker import FundamentalsWorker
from market_analysis.services import queries
from market_analysis.services.ingestors import etf as etf_ingestor


# Shared chart defaults (dark canvas).  Bright, high-contrast palette.
pg.setConfigOption("background", "#0b0f14")
pg.setConfigOption("foreground", "#e5e7eb")
pg.setConfigOption("antialias", True)


# Price + overlays: bright cyan, then three distinct high-contrast tones.
_PRICE_PEN = pg.mkPen("#22d3ee", width=2)           # cyan-400
_EMA_SHORT_PEN = pg.mkPen("#a3e635", width=2)       # lime-400
_EMA_MIDDLE_PEN = pg.mkPen("#fbbf24", width=2)      # amber-400
_EMA_LONG_PEN = pg.mkPen("#f472b6", width=2)        # pink-400
_RSI_PEN = pg.mkPen("#c084fc", width=2)             # violet-400
_GUIDE_PEN = pg.mkPen("#6b7280", width=1, style=Qt.PenStyle.DashLine)  # gray guides


# Quick-range toolbar presets.  ``None`` means "show all bars".
# Trading-day approximations: 21/63/126/252/504/1260 bars.
_RANGE_PRESETS: list[tuple[str, int | None]] = [
    ("1M", 21),
    ("3M", 63),
    ("6M", 126),
    ("1Y", 252),
    ("2Y", 504),
    ("5Y", 1260),
    ("All", None),
]

# Default window applied on symbol load.
_DEFAULT_WINDOW_BARS = 252


class CompanyTab(QWidget):
    """Per-ticker chart + fundamentals view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_symbols: list[str] = []
        self._current_symbol: str | None = None

        # Cached series for the current symbol (so toggling visibility is cheap).
        self._quotes: list = []
        self._ema_rows: list = []

        # Fundamentals worker state.
        self._fund_thread: QThread | None = None
        self._fund_worker: FundamentalsWorker | None = None
        # Labels for each fundamentals section — reused so refreshes
        # just mutate text instead of tearing down layouts.
        self._section_labels: dict[str, list[QLabel]] = {}

        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter)

        self.refresh_symbols()

    # -- Layout --------------------------------------------------------

    def _build_left(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.textChanged.connect(self._on_filter_changed)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        self._count_lbl = QLabel("0 symbols")
        layout.addWidget(self._count_lbl)

        return box

    def _build_right(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        # Header
        header = QHBoxLayout()
        self._symbol_lbl = QLabel("—")
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        self._symbol_lbl.setFont(font)
        self._range_lbl = QLabel("")
        self._delete_btn = QPushButton("Delete symbol…")
        self._delete_btn.setToolTip(
            "Permanently remove this company and all its price history + indicators. "
            "Use for orphaned symbols no longer held by any ETF."
        )
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        header.addWidget(self._symbol_lbl)
        header.addStretch(1)
        header.addWidget(self._range_lbl)
        header.addWidget(self._delete_btn)
        layout.addLayout(header)

        # Trace-visibility toggles.
        toggles = QHBoxLayout()
        toggles.addWidget(QLabel("Show:"))
        self._show_price = QCheckBox("Price"); self._show_price.setChecked(True)
        self._show_ema_short = QCheckBox("EMA 12"); self._show_ema_short.setChecked(True)
        self._show_ema_middle = QCheckBox("EMA 26"); self._show_ema_middle.setChecked(True)
        self._show_ema_long = QCheckBox("EMA 50"); self._show_ema_long.setChecked(True)
        self._show_rsi = QCheckBox("RSI 14"); self._show_rsi.setChecked(True)
        for cb in (
            self._show_price, self._show_ema_short, self._show_ema_middle,
            self._show_ema_long, self._show_rsi,
        ):
            cb.toggled.connect(self._redraw)
            toggles.addWidget(cb)
        toggles.addStretch(1)
        layout.addLayout(toggles)

        # Quick X-range toolbar (1M / 3M / 6M / 1Y / 2Y / 5Y / All).
        # Each button calls ``_set_x_window(days)`` — or None for "All".
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Range:"))
        for label, days in _RANGE_PRESETS:
            btn = QPushButton(label)
            btn.setFixedWidth(48)
            btn.clicked.connect(lambda _=False, d=days: self._set_x_window(d))
            range_row.addWidget(btn)
        range_row.addStretch(1)
        layout.addLayout(range_row)

        # Chart — price panel on top, RSI panel linked below.
        self._chart = pg.GraphicsLayoutWidget()
        self._price_plot = self._chart.addPlot(row=0, col=0, axisItems={"bottom": pg.DateAxisItem()})
        self._price_plot.showGrid(x=True, y=True, alpha=0.25)
        self._price_plot.addLegend(offset=(10, 10))
        # Auto-scale Y to traces inside the current X window — this is what
        # makes zoom/pan keep the visible series framed, instead of pyqtgraph
        # auto-fitting Y to the global data range (which pushes recent bars
        # off-screen after you zoom in on a subset).
        self._price_plot.getViewBox().setAutoVisible(y=True)
        self._price_plot.enableAutoRange(axis="y")

        self._rsi_plot = self._chart.addPlot(row=1, col=0, axisItems={"bottom": pg.DateAxisItem()})
        self._rsi_plot.showGrid(x=True, y=True, alpha=0.25)
        self._rsi_plot.setYRange(0, 100)
        self._rsi_plot.setMaximumHeight(180)
        self._rsi_plot.setXLink(self._price_plot)
        self._rsi_plot.addLine(y=70, pen=_GUIDE_PEN)
        self._rsi_plot.addLine(y=30, pen=_GUIDE_PEN)

        layout.addWidget(self._chart, 3)

        # Fundamentals (sectioned, scrollable)
        layout.addWidget(self._build_fundamentals_panel(), 2)

        return box

    def _build_fundamentals_panel(self) -> QWidget:
        outer = QGroupBox("Fundamentals")
        ol = QVBoxLayout(outer)

        # Header row: AV-populated name + status + fetch button.
        head = QHBoxLayout()
        self._fund_name_lbl = QLabel("—")
        f = QFont(); f.setBold(True); f.setPointSize(12)
        self._fund_name_lbl.setFont(f)
        head.addWidget(self._fund_name_lbl, 1)
        self._fund_status_lbl = QLabel("")
        self._fund_status_lbl.setStyleSheet("color: #9ca3af;")
        head.addWidget(self._fund_status_lbl)
        self._fetch_btn = QPushButton("Fetch fundamentals")
        self._fetch_btn.clicked.connect(self._on_fetch_fundamentals)
        head.addWidget(self._fetch_btn)
        ol.addLayout(head)

        # Scroll area for the section grid + description.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        grid = QGridLayout()
        grid.addWidget(self._build_section("identity",      "Identity"),      0, 0)
        grid.addWidget(self._build_section("valuation",     "Valuation"),     0, 1)
        grid.addWidget(self._build_section("profitability", "Profitability"), 1, 0)
        grid.addWidget(self._build_section("dividend",      "Dividend"),      1, 1)
        grid.addWidget(self._build_section("trading",       "Trading"),       2, 0)
        grid.addWidget(self._build_section("analyst",       "Analyst"),       2, 1)
        content_layout.addLayout(grid)

        # ETF memberships (simple chip row).
        self._etf_lbl = QLabel("")
        self._etf_lbl.setWordWrap(True)
        self._etf_lbl.setStyleSheet("color: #9ca3af; margin-top: 4px;")
        content_layout.addWidget(self._etf_lbl)

        # Company description as prose.
        desc_group = QGroupBox("Description")
        dl = QVBoxLayout(desc_group)
        self._desc = QTextBrowser()
        self._desc.setOpenExternalLinks(True)
        self._desc.setMinimumHeight(80)
        dl.addWidget(self._desc)
        content_layout.addWidget(desc_group, 1)

        scroll.setWidget(content)
        ol.addWidget(scroll, 1)

        return outer

    def _build_section(self, key: str, title: str) -> QGroupBox:
        """Build an empty (label, value) grid for a fundamentals section.

        Labels are stored in ``self._section_labels[key]`` so
        :meth:`_draw_fundamentals` only mutates text — no re-layout.
        The section is populated lazily on the first draw.
        """
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        form.setContentsMargins(8, 8, 8, 8)
        # Store the form so the first draw can populate it.
        box.setProperty("form", form)
        self._section_labels[key] = []
        return box

    # -- Public slots --------------------------------------------------

    def refresh_symbols(self) -> None:
        self._all_symbols = queries.list_symbols_with_quotes()
        self._apply_filter(self._filter.text())

    def select_symbol(self, symbol: str) -> None:
        """Programmatically select ``symbol`` and load its data.

        Used by cross-tab navigation (e.g. double-click from the
        Watchlist tab).  If the symbol isn't in the current filter,
        the filter is cleared first so it becomes visible.
        """
        sym = symbol.upper()
        if sym not in self._all_symbols:
            self._load(sym)
            return
        # Clear filter if it would hide the target row.
        if self._filter.text() and sym.find(self._filter.text().upper()) < 0:
            self._filter.clear()
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item and item.text() == sym:
                self._list.setCurrentItem(item)
                return
        self._load(sym)

    # -- Internals -----------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().upper()
        self._list.clear()
        shown = [s for s in self._all_symbols if needle in s] if needle else self._all_symbols
        for sym in shown:
            self._list.addItem(QListWidgetItem(sym))
        self._count_lbl.setText(f"{len(shown)} / {len(self._all_symbols)} symbols")

    def _on_filter_changed(self, text: str) -> None:
        self._apply_filter(text)

    def _on_selection_changed(self) -> None:
        items = self._list.selectedItems()
        if not items:
            return
        self._load(items[0].text())

    def _on_delete_clicked(self) -> None:
        sym = self._current_symbol
        if not sym:
            return
        # Show membership info so the user can spot a non-orphan before they pull the trigger.
        etf_doc = queries.load_etf(sym)
        membership = ""
        memberships = []
        try:
            from market_analysis.data import mongo as _mongo
            doc = _mongo.companies().find_one({"symbol": sym}, {"etf_memberships": 1, "_id": 0})
            memberships = list(doc.get("etf_memberships") or []) if doc else []
        except Exception:  # noqa: BLE001
            pass
        if memberships:
            membership = (
                f"\n\nWARNING: {sym} is still listed as a holding in "
                f"{', '.join(sorted(memberships))}. Removing it now will leave those "
                f"ETFs referencing a deleted company until the next daily update."
            )
        is_etf = etf_doc is not None
        if is_etf:
            membership += (
                f"\n\nWARNING: {sym} is itself a tracked ETF. Delete it from the "
                f"Admin → ETFs tab instead so its profile is also cleaned up."
            )

        resp = QMessageBox.question(
            self,
            "Delete symbol",
            f"Permanently delete {sym}?\n\n"
            f"This removes the company row, all price history, and all indicators "
            f"for {sym}. This cannot be undone.{membership}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return

        try:
            counts = etf_ingestor.delete_company(sym)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Delete failed", f"{sym}: {e}")
            return

        QMessageBox.information(
            self,
            "Symbol deleted",
            f"Removed {sym}: "
            f"company={counts['companies']}, "
            f"price bars={counts['price_history']:,}, "
            f"indicator rows={counts['indicators']:,}.",
        )
        self._current_symbol = None
        self._symbol_lbl.setText("—")
        self._delete_btn.setEnabled(False)
        self.refresh_symbols()
        self._apply_filter(self._filter.text())

    def _load(self, symbol: str) -> None:
        self._current_symbol = symbol
        self._symbol_lbl.setText(symbol)
        self._delete_btn.setEnabled(True)

        # Cache series so redraws don't re-hit Mongo.
        self._quotes = queries.load_quotes(symbol)
        self._ema_rows = queries.load_ema_stack(symbol, stack=0)
        self._rsi_rows = queries.load_rsi(symbol, stack=0)

        self._redraw()
        # Default to roughly the last trading year on load; falls back to
        # full range when fewer bars are available.
        self._set_x_window(_DEFAULT_WINDOW_BARS)
        self._draw_fundamentals(symbol)

    # -- Chart range control -------------------------------------------

    def _set_x_window(self, n_bars: int | None) -> None:
        """Set the price plot's X window to the last ``n_bars`` bars.

        ``n_bars=None`` resets to full range.  The RSI plot is X-linked
        and follows automatically; Y ranges on both plots re-auto-scale
        to the visible slice thanks to ``setAutoVisible(y=True)``.
        """
        bounds = self._x_window_bounds(self._quotes, n_bars)
        if bounds is None:
            self._price_plot.enableAutoRange(axis="x")
            return
        start, end = bounds
        self._price_plot.setXRange(start, end, padding=0.02)

    @staticmethod
    def _x_window_bounds(
        quotes: list, n_bars: int | None
    ) -> tuple[float, float] | None:
        """Return ``(start_ts, end_ts)`` for the last ``n_bars`` of ``quotes``.

        Returns ``None`` when the whole range should be shown (no quotes,
        ``n_bars`` is ``None``, or ``n_bars`` covers everything).  Used by
        :meth:`_set_x_window`; factored out so the slicing logic is unit
        testable without a live pyqtgraph ViewBox.
        """
        if not quotes or n_bars is None or n_bars <= 0:
            return None
        if n_bars >= len(quotes):
            return None
        start = quotes[-n_bars].date.timestamp()
        end = quotes[-1].date.timestamp()
        return (start, end)

    # -- Chart drawing -------------------------------------------------

    def _redraw(self) -> None:
        self._price_plot.clear()
        if self._price_plot.legend is not None:
            self._price_plot.legend.clear()
        self._rsi_plot.clear()
        # Re-add RSI guide lines (clear wipes them).
        self._rsi_plot.addLine(y=70, pen=_GUIDE_PEN)
        self._rsi_plot.addLine(y=30, pen=_GUIDE_PEN)

        if not self._quotes:
            self._range_lbl.setText("no quotes")
            return

        qx = [q.date.timestamp() for q in self._quotes]
        qy = [q.adjusted_close if q.adjusted_close is not None else q.close
              for q in self._quotes]

        if self._show_price.isChecked():
            self._price_plot.plot(qx, qy, pen=_PRICE_PEN, name="Adjusted close")

        if self._ema_rows:
            ex = [r.date.timestamp() for r in self._ema_rows]
            for cb, attr, pen, label in (
                (self._show_ema_short, "short", _EMA_SHORT_PEN, "EMA 12"),
                (self._show_ema_middle, "middle", _EMA_MIDDLE_PEN, "EMA 26"),
                (self._show_ema_long, "long", _EMA_LONG_PEN, "EMA 50"),
            ):
                if not cb.isChecked():
                    continue
                xs: list[float] = []
                ys: list[float] = []
                for r, t in zip(self._ema_rows, ex):
                    v = getattr(r, attr)
                    if v is None:
                        continue
                    xs.append(t)
                    ys.append(v)
                if xs:
                    self._price_plot.plot(xs, ys, pen=pen, name=label)

        if self._show_rsi.isChecked() and self._rsi_rows:
            rx = [r.date.timestamp() for r in self._rsi_rows]
            ry = [r.value for r in self._rsi_rows]
            self._rsi_plot.plot(rx, ry, pen=_RSI_PEN, name="RSI 14")

        # Re-enable Y auto-range after ``clear()``; ``setAutoVisible(y=True)``
        # (configured once at build time) scopes the auto-range to samples
        # inside the current X window, so zoom/pan still behaves.  Without
        # this call the price panel stays at its stale Y range and traces
        # render off-screen.
        self._price_plot.enableAutoRange(axis="y")
        self._rsi_plot.enableAutoRange(axis="y")

        first = self._quotes[0].date.date()
        last = self._quotes[-1].date.date()
        self._range_lbl.setText(f"{first} → {last}  ({len(self._quotes):,} bars)")

    def _draw_fundamentals(self, symbol: str) -> None:
        view = queries.load_fundamentals_view(symbol)

        self._fund_name_lbl.setText(view.name or symbol)
        if view.has_data:
            self._fund_status_lbl.setText("")
        else:
            self._fund_status_lbl.setText("No AV OVERVIEW ingested yet")

        sections = {
            "identity": view.identity,
            "valuation": view.valuation,
            "profitability": view.profitability,
            "dividend": view.dividend,
            "trading": view.trading,
            "analyst": view.analyst,
        }
        for key, rows in sections.items():
            self._populate_section(key, rows)

        # ETF membership chips — helpful context for equity tickers.
        etf_doc = queries.load_etf(symbol)
        memberships = view.etf_memberships
        if memberships:
            self._etf_lbl.setText("ETF memberships: " + ", ".join(memberships))
        elif etf_doc:
            provider = etf_doc.get("provider") or "—"
            ratio = etf_doc.get("expense_ratio")
            ratio_s = f"{ratio:.2%}" if ratio is not None else "—"
            holdings_n = len(etf_doc.get("holdings", []))
            self._etf_lbl.setText(
                f"This symbol is an ETF — provider: {provider}  ·  "
                f"expense ratio: {ratio_s}  ·  {holdings_n} holdings"
            )
        else:
            self._etf_lbl.setText("")

        # Description as plain (non-HTML) prose — safer given AV strings.
        desc = view.description or ""
        self._desc.setPlainText(desc or "(no description on file)")

    def _populate_section(
        self, key: str, rows: list[tuple[str, str]]
    ) -> None:
        """Replace the section's form rows with ``rows`` (label, value)."""
        # Locate the form stashed on the groupbox.
        box = self._section_groupbox(key)
        form: QFormLayout = box.property("form")
        while form.rowCount():
            form.removeRow(0)
        for label, value in rows:
            vlab = QLabel(value)
            vlab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label + ":", vlab)

    def _section_groupbox(self, key: str) -> QGroupBox:
        """Find the groupbox created by ``_build_section(key, …)``."""
        # We stored the form on the box via setProperty; the box is
        # the only groupbox whose ``form`` property matches.  Simpler
        # is to iterate the grid children, but we kept a handle on
        # each box indirectly via the form.  Walk the widget tree.
        for box in self.findChildren(QGroupBox):
            f = box.property("form")
            if f is not None and self._section_labels_key(box) == key:
                return box
        raise KeyError(key)

    def _section_labels_key(self, box: QGroupBox) -> str | None:
        """Map a groupbox back to its section key (by title, case-folded)."""
        mapping = {
            "Identity": "identity",
            "Valuation": "valuation",
            "Profitability": "profitability",
            "Dividend": "dividend",
            "Trading": "trading",
            "Analyst": "analyst",
        }
        return mapping.get(box.title())

    # -- Fundamentals fetch --------------------------------------------

    def _on_fetch_fundamentals(self) -> None:
        if self._current_symbol is None or self._fund_thread is not None:
            return
        sym = self._current_symbol
        self._fetch_btn.setEnabled(False)
        self._fund_status_lbl.setText("fetching…")

        self._fund_thread = QThread(self)
        self._fund_worker = FundamentalsWorker(sym, cache=True)
        self._fund_worker.moveToThread(self._fund_thread)
        self._fund_thread.started.connect(self._fund_worker.run)
        self._fund_worker.done.connect(self._on_fundamentals_done)
        self._fund_thread.start()

    def _on_fundamentals_done(self, success: bool, summary: str) -> None:
        if self._fund_thread is not None:
            self._fund_thread.quit()
            self._fund_thread.wait()
        self._fund_thread = None
        self._fund_worker = None
        self._fetch_btn.setEnabled(True)
        self._fund_status_lbl.setText(
            "" if success else f"fetch failed: {summary}"
        )
        if success and self._current_symbol is not None:
            self._draw_fundamentals(self._current_symbol)


