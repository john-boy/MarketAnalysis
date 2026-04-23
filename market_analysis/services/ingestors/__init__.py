"""Ingestors — one module per data domain.

Each ingestor orchestrates: request → adapter → normalize →
upsert.  Ingestors are callable from the poller and from CLI
scripts in ``scripts/``.
"""
