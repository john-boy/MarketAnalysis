Sounds good. Progress so far:

- Plan locked at v0.3; documented in `docs/plan.md` and `docs/decisions.md` (ADR-0000 through ADR-0007).
- Ticket 1.0 — scaffold complete: package layout, deps, launcher, README, docs folder.
- Ticket 1.1 — config loader complete: typed Pydantic `Settings`, secrets masking, 5 passing tests, placeholder window showing the loaded summary.
- Placeholder app runs cleanly (`python main.py`).

Pending when you're back:

- Copy AV key into `config/secrets.toml` (from the example).
- Decide whether to fold the Schwab OAuth notes (HTTPS-only, `127.0.0.1:8182`, exact-match, self-signed cert, `schwab-py`) into Phase 4 of the plan.
- Ticket 1.2 — Mongo bootstrap + `scripts/migrate_from_prototype.py`.

Nothing is committed to git yet — that's yours to do when ready. Have a good evening.