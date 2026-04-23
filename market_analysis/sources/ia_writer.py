"""iA Writer folder adapter.

Indexes markdown files in a configured iCloud folder, parses YAML
front-matter (themes, tickers, type, home), and emits change events
via ``watchdog``.  Also provides a helper that opens a file in iA
Writer via ``open -a "iA Writer" <path>``.

Implemented in Phase 5.
"""
