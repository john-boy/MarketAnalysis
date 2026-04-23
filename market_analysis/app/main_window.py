"""Main application window.

Phase 1 slice: hosts Admin and Company tabs inside a ``QTabWidget``,
with a minimal File / View menu and a status bar showing Mongo
health.  Dockable watchlist / theme panels and the remaining tabs
(Market, Portfolio, Filings, News, Themes) arrive in later phases.
"""

from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMainWindow, QMessageBox, QTabWidget

from market_analysis.app.tabs.admin_tab import AdminTab
from market_analysis.app.tabs.company_tab import CompanyTab
from market_analysis.services import queries


class MainWindow(QMainWindow):
    """Top-level window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Market Analysis")
        self.resize(1440, 900)

        self._tabs = QTabWidget(self)
        self._admin = AdminTab(self)
        self._company = CompanyTab(self)

        self._tabs.addTab(self._admin, "Admin")
        self._tabs.addTab(self._company, "Company")
        self.setCentralWidget(self._tabs)

        # Refresh the Company tab's symbol list whenever an ingest finishes.
        self._admin.ingest_finished.connect(self._on_ingest_finished)

        self._build_menu()
        self._update_status_bar()

    # -- Menus ---------------------------------------------------------

    def _build_menu(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        quit_act = QAction("&Quit", self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        view_menu = menubar.addMenu("&View")
        refresh_act = QAction("&Refresh", self)
        refresh_act.setShortcut(QKeySequence.StandardKey.Refresh)  # F5
        refresh_act.triggered.connect(self.refresh_all)
        view_menu.addAction(refresh_act)

        help_menu = menubar.addMenu("&Help")
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

    # -- Slots ---------------------------------------------------------

    def refresh_all(self) -> None:
        self._admin.refresh()
        self._company.refresh_symbols()
        self._update_status_bar()

    def _on_ingest_finished(self, success: bool, summary: str) -> None:
        self._company.refresh_symbols()
        self._update_status_bar()
        msg = ("Ingest complete — " if success else "Ingest failed — ") + summary
        self.statusBar().showMessage(msg, 10_000)

    def _update_status_bar(self) -> None:
        h = queries.db_health()
        if not h.reachable:
            self.statusBar().showMessage(f"Mongo UNREACHABLE at {h.uri}")
            return
        quotes = h.counts.get("daily_quotes", 0)
        companies = h.counts.get("companies", 0)
        indicators = h.counts.get("indicators", 0)
        schema = "—" if h.schema_version is None else f"v{h.schema_version}"
        self.statusBar().showMessage(
            f"{h.database} [schema {schema}]  "
            f"quotes: {quotes:,}   indicators: {indicators:,}   "
            f"companies: {companies:,}"
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Market Analysis",
            "Market Analysis — Phase 1\n"
            "Desktop portfolio + market-analysis workbench.\n"
            "© Greenthread Companies",
        )
