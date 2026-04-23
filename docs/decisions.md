# Architectural Decision Log

Short entries. One decision per heading. Newest on top.

---

## ADR-0008 — Time-series collections for prices and indicators

**Date:** 2026-04-22
**Decision:** `daily_quotes` and `indicators` are MongoDB **time-series**
collections with `timeField="date"`, `metaField="metadata"`, and 1-day
buckets (`bucketRoundingSeconds=bucketMaxSpanSeconds=86400`).  Secondary
(non-unique) indexes only.

**Why metaField="metadata".** The MarketInfo prototype *declared*
`metaField="symbol"` (and `"indicator"`) but *stored* all meta fields in
a `metadata` sub-document, leaving the declared metaField pointing at a
non-existent top-level field.  The migration corrects this by declaring
`metaField="metadata"` to match the actual data shape.  Data is copied
through unchanged.

**Uniqueness.** MongoDB does not support unique indexes on time-series
collections.  Upserts during ingest must therefore guard against
duplicates at the application layer (see Ticket 1.5).

## ADR-0007 — Ingest-tag namespace: flat

**Date:** 2026-04-21
**Decision:** Use a flat tag namespace (`news_sentiment`, `options_chain`)
for per-ticker opt-in data streams. Regroup only if the list exceeds ~12
tags. Currently six.
**Theme namespace is separate** (groups + themes, see ADR-0003).

## ADR-0006 — Transactions deferred

**Date:** 2026-04-21
**Decision:** v1 syncs **current position snapshots only**. Transaction
history is deferred. Each sync overwrites the prior snapshot; an
`account_sync_log` entry preserves audit history.
**Reason:** Faster to ship; transactions are primarily needed for P&L
history, which is not a day-1 requirement.

## ADR-0005 — Portfolio = actual holdings, multi-account, mixed access

**Date:** 2026-04-21
**Decision:** Portfolio management covers real holdings across multiple
accounts. Schwab accounts sync via API; non-Schwab accounts are manual
(with a paste-a-table UI and a CSV import script) until/unless APIs
become available. Three Schwab accounts seeded: IRA, Joint, Futures.
**Open:** whether the Schwab Trader API returns futures positions in
`/accounts/{id}/positions` — to be verified when the API key arrives.

## ADR-0004 — Edgar_Monitor: federated, not merged

**Date:** 2026-04-21
**Decision:** Edgar_Monitor continues as a separate application. It will
write tagged filings into the shared `MarketAnalysis` Mongo in a `filings`
collection. Market Analysis reads only.
**Reason:** Preserves working software; the JS monitor and the Python
analysis app each stay focused on what they do well.

## ADR-0003 — Theme model: groups + themes, multi-valued, tag-based

**Date:** 2026-04-21
**Decision:** Themes are organized into **theme groups** (one level of
grouping, no deeper). Documents tag one or more themes directly; group
membership is inherited via the themes. Multi-theme is supported (a
SpaceX document is also `ai` and `social-media`).
**Data model:** `theme_groups`, `themes`, and `theme_documents` are
first-class Mongo collections.
**Reason:** Flat themes insufficient; full hierarchies overkill; tag-based
matches Edgar_Monitor philosophy.

## ADR-0002 — UI framework: PySide6, not Streamlit

**Date:** 2026-04-21
**Decision:** PySide6 desktop app.
**Reason:** Background worker, stateful multi-pane workflows, rich tables,
iA Writer integration via `QFileSystemWatcher`, OS integration (tray, file
dialogs, shortcuts) — all native in Qt. Streamlit's script-rerun model
would fight each of these.
**Cost:** more code per screen; steeper learning curve on Qt's model/view;
the MarketInfo UI (Streamlit) does not port, only the data layer does.

## ADR-0001 — Data source strategy: AV primary, Schwab gap-fill + accounts

**Date:** 2026-04-21
**Decision:** Alpha Vantage is the primary data source (paid subscription,
broad fundamentals + news sentiment). Schwab supplements for
(a) newly-issued IPOs that AV hasn't indexed yet, (b) option chains,
(c) real-time quotes, and (d) actual account/position sync.
**Reason:** AV has universal-coverage gaps, particularly on new IPOs.
Schwab's broker-direct feed has whatever is tradable in the user's account
from the first day of trading, but shallow fundamentals. The two are
complementary.

## ADR-0000 — Storage: MongoDB, reuse the `MarketAnalysis` name

**Date:** 2026-04-21
**Decision:** Continue with MongoDB (existing prototype DB). The current
`MarketAnalysis` DB is renamed to `MarketAnalysis_Prototype`, and a fresh
`MarketAnalysis` is created. `daily_quotes` and `indicators` collections
are copied forward; everything else is reingested fresh.
**Reason:** Schema churn on `companies` and `etf` during rebuild would
require migrations anyway; quotes and indicators are expensive to
reacquire and stable in shape.
