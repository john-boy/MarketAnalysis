"""Service layer — pure Python, no Qt imports.

Orchestrates the data layer and external-source adapters.  UI code
in ``market_analysis.app`` obtains data by calling services, never
by importing ``market_analysis.data`` or ``market_analysis.sources``
directly.
"""
