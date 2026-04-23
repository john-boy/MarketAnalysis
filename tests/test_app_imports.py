"""Smoke test: the UI module graph imports cleanly.

We don't spin up a ``QApplication`` here (pytest-qt isn't required for
this test); we just confirm that every UI module can be imported in
isolation, which catches syntax errors, circular imports, and missing
PySide6 symbols.
"""

from __future__ import annotations

import importlib


UI_MODULES = (
    "market_analysis.app.main",
    "market_analysis.app.main_window",
    "market_analysis.app.tabs.admin_tab",
    "market_analysis.app.tabs.company_tab",
    "market_analysis.app.widgets.ingest_worker",
    "market_analysis.services.queries",
)


def test_ui_modules_import():
    for name in UI_MODULES:
        importlib.import_module(name)
