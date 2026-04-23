Proceeding with Ticket 1.0. I'll lay out the package, write the documentation, create the README, and replace the sample `main.py` with a real launcher. I'll stage everything but leave the commit to you (per our working convention — only commit on explicit request).

Now the package skeleton — module stubs with docstrings describing each file's responsibility. Batching the writes.

Let me verify the tree:

## Ticket 1.0 — complete

### What's on disk

**Documentation (`docs/`)**
- `plan.md` — the full v0.3 rolling plan (architecture, data sources & routing table, theme model, accounts model, ingest tags, poller cadence, UI structure, phase roadmap, Phase 1 tickets, open items, revision log).
- `decisions.md` — architectural decision log, ADR-0000 through ADR-0007 (storage, source strategy, UI framework, theme model, Edgar_Monitor federation, portfolio scope, transactions-deferred, ingest-tag namespace).
- `docs/README.md` — index.

**Top level**
- `README.md` — GitHub-style: status, tech stack, data sources, project layout, quickstart, related projects.
- `pyproject.toml` — pinned deps (pyside6, pymongo, pyqtgraph, apscheduler, requests, pydantic, markdown, watchdog, python-dateutil; dev extras: pytest, pytest-qt, ruff). Console script `market-analysis`.
- `.gitignore` — Python, venvs, IDE, `config/secrets.toml` explicitly blocked.
- `main.py` — replaced the PyCharm sample; now a one-line launcher that calls `market_analysis.app.main.run()`.

**Package skeleton (`market_analysis/`)**
- `app/` — `main.py` (QApplication bootstrap), `main_window.py` (placeholder QMainWindow that runs today), `tabs/` (8 tab stubs: market, theme, company, portfolio, watchlist, news, filings, admin), `widgets/`.
- `services/` — `config`, `source_router`, `watchlist`, `themes`, `indicators`, `risk` (stub class), `poller`, `ingestors/{etf,prices}`.
- `sources/` — `alpha_vantage`, `schwab`, `edgar`, `ia_writer`.
- `data/` — `mongo.py`, `models/`, `schemas/`, `migrations/`.

**Runtime dirs**
- `config/settings.toml` (non-secret), `config/secrets.toml.example` (template — gitignored when copied without `.example`).
- `scripts/` — `migrate_from_prototype.py`, `ingest_etf.py`.
- `tests/` — empty package ready.

### Architectural invariants encoded in docstrings

- `app/` may import Qt; `services/` and `data/` may **not**.
- Tabs talk to `services/`, never directly to `data/` or `sources/`.
- Adapters (`sources/`) are thin — HTTP + auth only; persistence belongs to services.

### Ready to run (scaffold-level)

```
pip install -e '.[dev]'
python main.py
```

You'll see a placeholder QMainWindow. No functionality yet — that's Tickets 1.1–1.6.

### Not done (deliberately)

- **No git commit.** Per our working convention, I stage but don't commit unless you ask. When you're ready, a reasonable first commit message:
  > `scaffold: package layout, deps, documentation, launcher (Ticket 1.0)`
- No remote added, no push.

### Next

When you're ready, say the word and I'll start **Ticket 1.1 — Config + secrets loader**. It's small and self-contained, a good next step while you work on the Schwab key.