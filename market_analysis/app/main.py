"""Application entrypoint.

Wires the Qt application, builds the main window, and starts the event loop.
"""

from __future__ import annotations

import sys


def run() -> int:
    """Start the Market Analysis GUI.

    Returns the Qt application exit code.
    """
    # Imported lazily so ``python -c "import market_analysis"`` doesn't pull Qt.
    from PySide6.QtWidgets import QApplication

    from market_analysis.app.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Market Analysis")
    app.setOrganizationName("Greenthread Companies")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(run())
