"""Risk engine — stub.

Skeleton for position-sizing, stop, concentration, and FOMO-guard
rules.  Rules are defined in configuration and ship disabled on day 1;
they are enabled as they are formalized.

See ``docs/plan.md`` § 8 (Phase 7).
"""


class RiskEngine:  # noqa: D101  — implemented in Phase 7
    def position_size_rule(self, account, candidate):
        raise NotImplementedError

    def stop_rule(self, position):
        raise NotImplementedError

    def concentration_report(self, portfolio):
        raise NotImplementedError

    def fomo_guard(self, candidate, recent_activity):
        raise NotImplementedError
