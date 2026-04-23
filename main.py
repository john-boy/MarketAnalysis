"""Top-level launcher.

The real entrypoint lives in ``market_analysis.app.main``.  This file exists
so PyCharm's default run configuration (main.py) still works.
"""

from market_analysis.app.main import run


if __name__ == "__main__":
    run()
