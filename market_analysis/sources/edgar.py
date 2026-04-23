"""EDGAR bridge.

In the federated model (ADR-0004), the Edgar_Monitor application
writes 8-K filings directly into the shared Mongo ``filings``
collection.  This module exposes read helpers for Market Analysis
tabs and ingestors; it does not fetch from SEC directly.

Implemented in Phase 6.
"""
