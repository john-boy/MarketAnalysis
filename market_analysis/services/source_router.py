"""Coverage-aware data source dispatch.

Given a request ("daily prices for RKLB since 2025-10-01"), chooses
the right adapter (Alpha Vantage, Schwab, ...) per the routing table
in ``docs/plan.md`` § 4.5, handles fallbacks, and records provenance
so the UI can surface where a value came from.

Implemented in Phase 4.
"""
