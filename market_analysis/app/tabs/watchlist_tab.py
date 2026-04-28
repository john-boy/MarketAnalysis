"""Watchlist tab — master list of tracked tickers.

User-maintained list of symbols that merit attention beyond ETF
holdings coverage.  Rows show symbol, themes, ingest tags, source of
origin, added date, last edited date, and the date of the most
recent price bar (coverage freshness).

Double-clicking a row emits :sig:`open_symbol`, which the main window
routes to the Company tab.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from market_analysis.services import queries, watchlist as wl_svc


_TAGS_PLACEHOLDER = "news, filings, earnings"
_THEMES_PLACEHOLDER = "ai-infra, biotech"


class WatchlistTab(QWidget):
    """Watchlist CRUD surface."""

    #: Emitted when the user double-clicks a row (``str`` = symbol).
    open_symbol = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

        root = QVBoxLayout(self)
        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_table(), 1)
        root.addWidget(self._build_editor())
        self.refresh()

    # -- Layout --------------------------------------------------------

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by symbol…")
        self._filter.textChanged.connect(self._apply_filter)
        bar.addWidget(self._filter, 1)

        self._count_lbl = QLabel("0 entries")
        bar.addWidget(self._count_lbl)

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        bar.addWidget(refresh)
        return bar

    def _build_table(self) -> QWidget:
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "Symbol", "Themes", "Ingest tags", "Source",
            "Added", "Edited", "Last bar",
        ])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.itemDoubleClicked.connect(self._on_row_double_clicked)
        self._table.setSortingEnabled(True)
        return self._table

    def _build_editor(self) -> QWidget:
        group = QGroupBox("Edit / add")
        layout = QVBoxLayout(group)

        # Row 1: symbol + themes + ingest tags
        r1 = QHBoxLayout()
        self._edit_symbol = QLineEdit()
        self._edit_symbol.setPlaceholderText("Symbol (e.g. AAPL)")
        self._edit_symbol.setMaximumWidth(140)
        self._edit_themes = QLineEdit()
        self._edit_themes.setPlaceholderText(
            f"Themes (comma-separated, e.g. {_THEMES_PLACEHOLDER})"
        )
        self._edit_tags = QLineEdit()
        self._edit_tags.setPlaceholderText(
            f"Ingest tags (comma-separated, e.g. {_TAGS_PLACEHOLDER})"
        )
        r1.addWidget(QLabel("Symbol:"))
        r1.addWidget(self._edit_symbol)
        r1.addWidget(QLabel("Themes:"))
        r1.addWidget(self._edit_themes, 1)
        r1.addWidget(QLabel("Tags:"))
        r1.addWidget(self._edit_tags, 1)
        layout.addLayout(r1)

        # Row 2: notes
        self._edit_notes = QTextEdit()
        self._edit_notes.setPlaceholderText("Notes…")
        self._edit_notes.setMaximumHeight(60)
        layout.addWidget(self._edit_notes)

        # Row 3: action buttons
        r3 = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._on_add_clicked)
        save_btn = QPushButton("Save changes to selected")
        save_btn.clicked.connect(self._on_save_clicked)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._on_remove_clicked)
        r3.addWidget(add_btn)
        r3.addWidget(save_btn)
        r3.addWidget(remove_btn)
        r3.addStretch(1)
        layout.addLayout(r3)

        return group

    # -- Public --------------------------------------------------------

    def refresh(self) -> None:
        """Reload watchlist rows from the DB."""
        self._rows = queries.list_watchlist()
        self._apply_filter(self._filter.text())

    # -- Table rendering -----------------------------------------------

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().upper()
        rows = [r for r in self._rows if needle in r["symbol"]] if needle else self._rows
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._set_row(i, r)
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(3)
        self._table.resizeColumnToContents(4)
        self._table.resizeColumnToContents(5)
        self._table.resizeColumnToContents(6)
        self._table.setSortingEnabled(True)
        self._count_lbl.setText(
            f"{len(rows)} / {len(self._rows)} entries"
        )

    def _set_row(self, i: int, r: dict[str, Any]) -> None:
        self._table.setItem(i, 0, QTableWidgetItem(r["symbol"]))
        self._table.setItem(i, 1, QTableWidgetItem(", ".join(r.get("themes") or [])))
        self._table.setItem(i, 2, QTableWidgetItem(", ".join(r.get("ingest_tags") or [])))
        self._table.setItem(i, 3, QTableWidgetItem(r.get("source_of_origin") or "—"))
        self._table.setItem(i, 4, QTableWidgetItem(_fmt_date(r.get("added_at"))))
        self._table.setItem(i, 5, QTableWidgetItem(_fmt_date(r.get("last_updated"))))
        last = r.get("last_quote_date")
        item = QTableWidgetItem(_fmt_date(last) if last else "no data")
        if not last:
            item.setForeground(Qt.GlobalColor.red)
        self._table.setItem(i, 6, item)

    # -- Selection -----------------------------------------------------

    def _selected_symbol(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        return item.text() if item else None

    def _on_selection_changed(self) -> None:
        sym = self._selected_symbol()
        if not sym:
            return
        r = next((x for x in self._rows if x["symbol"] == sym), None)
        if r is None:
            return
        self._edit_symbol.setText(r["symbol"])
        self._edit_themes.setText(", ".join(r.get("themes") or []))
        self._edit_tags.setText(", ".join(r.get("ingest_tags") or []))
        self._edit_notes.setPlainText(r.get("notes") or "")

    def _on_row_double_clicked(self, item: QTableWidgetItem) -> None:
        sym_item = self._table.item(item.row(), 0)
        if sym_item:
            self.open_symbol.emit(sym_item.text())

    # -- Actions -------------------------------------------------------

    def _on_add_clicked(self) -> None:
        sym = self._edit_symbol.text().strip().upper()
        if not sym:
            QMessageBox.warning(self, "Add watchlist entry",
                                "Enter a symbol first.")
            return
        inserted = wl_svc.add_entry(
            sym,
            themes=_split_csv(self._edit_themes.text()),
            ingest_tags=_split_csv(self._edit_tags.text()),
            notes=self._edit_notes.toPlainText().strip() or None,
        )
        if not inserted:
            QMessageBox.information(
                self, "Add watchlist entry",
                f"{sym} is already on the watchlist.  Use "
                "'Save changes to selected' to edit it.",
            )
        self._clear_editor()
        self.refresh()

    def _on_save_clicked(self) -> None:
        sym = self._selected_symbol()
        if not sym:
            QMessageBox.warning(
                self, "Save changes",
                "Select a row first.",
            )
            return
        notes_txt = self._edit_notes.toPlainText().strip()
        wl_svc.update_entry(
            sym,
            themes=_split_csv(self._edit_themes.text()),
            ingest_tags=_split_csv(self._edit_tags.text()),
            notes=notes_txt if notes_txt else "",
        )
        self.refresh()

    def _on_remove_clicked(self) -> None:
        sym = self._selected_symbol()
        if not sym:
            return
        resp = QMessageBox.question(
            self,
            "Remove watchlist entry",
            f"Remove {sym} from the watchlist?\n\n"
            "Any price history and indicators under this symbol are "
            "preserved.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        wl_svc.remove_entry(sym)
        self._clear_editor()
        self.refresh()

    def _clear_editor(self) -> None:
        self._edit_symbol.clear()
        self._edit_themes.clear()
        self._edit_tags.clear()
        self._edit_notes.clear()


# -- Helpers --------------------------------------------------------------


def _split_csv(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def _fmt_date(d: datetime | None) -> str:
    if d is None:
        return "—"
    return d.strftime("%Y-%m-%d")
