"""Background poller.

APScheduler-driven job runner hosted in a ``QThread``.  Runs the
daily market-close-plus-two-hours cycle described in
``docs/plan.md`` § 6, emits Qt signals for UI consumption.

Implemented in Phase 3.
"""
