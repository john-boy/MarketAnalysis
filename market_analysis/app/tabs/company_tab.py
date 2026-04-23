"""Company tab — single-ticker detail view.

Left: filterable list of symbols present in ``daily_quotes``.
Right: a stacked chart with a price panel (adjusted close + three
EMA overlays) and a linked RSI panel below it (0-100, 30/70 lines);
a visibility toggle row above the chart controls which traces are
drawn.  Fundamentals form + raw-JSON pane round out the right side.

Filings and news panels arrive in later phases.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from market_analysis.services import queries


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


class CompanyTab(QWidget):
    """Per-ticker chart + fundamentals view."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_symbols: list[str] = []
        self._current_symbol: str | None = None

        # Cached series for the current symbol (so toggling visibility is cheap).
        self._quotes: list = []
        self._ema_rows: list = []

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
        header.addWidget(self._symbol_lbl)
        header.addStretch(1)
        header.addWidget(self._range_lbl)
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

        # Chart — price panel on top, RSI panel linked below.
        self._chart = pg.GraphicsLayoutWidget()
        self._price_plot = self._chart.addPlot(row=0, col=0, axisItems={"bottom": pg.DateAxisItem()})
        self._price_plot.showGrid(x=True, y=True, alpha=0.25)
        self._price_plot.addLegend(offset=(10, 10))

        self._rsi_plot = self._chart.addPlot(row=1, col=0, axisItems={"bottom": pg.DateAxisItem()})
        self._rsi_plot.showGrid(x=True, y=True, alpha=0.25)
        self._rsi_plot.setYRange(0, 100)
        self._rsi_plot.setMaximumHeight(180)
        self._rsi_plot.setXLink(self._price_plot)
        self._rsi_plot.addLine(y=70, pen=_GUIDE_PEN)
        self._rsi_plot.addLine(y=30, pen=_GUIDE_PEN)

        layout.addWidget(self._chart, 3)

        # Fundamentals
        fund = QGroupBox("Fundamentals")
        self._fund_form = QFormLayout(fund)
        self._fund_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(fund, 1)

        # Raw pane
        raw = QGroupBox("Raw")
        rg = QVBoxLayout(raw)
        self._raw = QPlainTextEdit()
        self._raw.setReadOnly(True)
        self._raw.setFont(QFont("Menlo", 10))
        rg.addWidget(self._raw)
        layout.addWidget(raw, 1)

        return box

    # -- Public slots --------------------------------------------------

    def refresh_symbols(self) -> None:
        self._all_symbols = queries.list_symbols_with_quotes()
        self._apply_filter(self._filter.text())

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

    def _load(self, symbol: str) -> None:
        self._current_symbol = symbol
        self._symbol_lbl.setText(symbol)

        # Cache series so redraws don't re-hit Mongo.
        self._quotes = queries.load_quotes(symbol)
        self._ema_rows = queries.load_ema_stack(symbol, stack=0)
        self._rsi_rows = queries.load_rsi(symbol, stack=0)

        self._redraw()
        self._draw_fundamentals(symbol)

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

        first = self._quotes[0].date.date()
        last = self._quotes[-1].date.date()
        self._range_lbl.setText(f"{first} → {last}  ({len(self._quotes):,} bars)")

    def _draw_fundamentals(self, symbol: str) -> None:
        while self._fund_form.rowCount():
            self._fund_form.removeRow(0)

        company = queries.load_company(symbol) or {}
        etf = queries.load_etf(symbol)

        shown = False
        for key in ("name", "sector", "industry", "exchange", "country",
                    "currency", "cik", "fiscal_year_end"):
            val = company.get(key)
            if val:
                self._fund_form.addRow(key.replace("_", " ").title() + ":",
                                       QLabel(str(val)))
                shown = True
        if company.get("etf_memberships"):
            self._fund_form.addRow("ETF Memberships:",
                                   QLabel(", ".join(company["etf_memberships"])))
            shown = True

        if etf:
            self._fund_form.addRow("ETF Provider:", QLabel(str(etf.get("provider") or "—")))
            self._fund_form.addRow(
                "ETF Expense Ratio:",
                QLabel("—" if etf.get("expense_ratio") is None
                       else f"{etf['expense_ratio']:.4%}"),
            )
            self._fund_form.addRow("ETF Holdings:",
                                   QLabel(f"{len(etf.get('holdings', []))}"))
            shown = True

        if not shown:
            self._fund_form.addRow("", QLabel("No fundamentals on file."))

        payload: dict[str, Any] = {}
        if company:
            payload["companies"] = _truncate_for_display(company)
        if etf:
            payload["etf"] = _truncate_for_display(etf)
        self._raw.setPlainText(_pretty(payload) if payload else "—")


# -- Helpers --------------------------------------------------------------


def _truncate_for_display(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in doc.items():
        if isinstance(v, list) and len(v) > 5:
            out[k] = v[:5] + [f"… {len(v) - 5} more"]
        elif isinstance(v, dict) and len(v) > 10:
            items = list(v.items())[:10]
            out[k] = dict(items) | {"__truncated__": f"{len(v) - 10} more keys"}
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _pretty(obj: Any, indent: int = 0) -> str:
    pad = "  " * indent
    if isinstance(obj, dict):
        lines = [pad + "{"]
        for k, v in obj.items():
            lines.append(f"{pad}  {k!r}: {_pretty_inline(v)}")
        lines.append(pad + "}")
        return "\n".join(lines)
    return _pretty_inline(obj)


def _pretty_inline(v: Any) -> str:
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k!r}: {_pretty_inline(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_pretty_inline(x) for x in v) + "]"
    return repr(v)
