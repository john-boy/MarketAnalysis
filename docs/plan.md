# Market Analysis — Rolling Build Plan

**Status:** Phase 1 complete — ready for Phase 2
**Last revision:** v0.5 (2026-04-22)
**This is a living document.** Update as decisions evolve.

---

## 1. Goals

Market Analysis is a comprehensive desktop application supporting:

- **Portfolio management** — actual holdings across multiple brokerage
  accounts, synchronized via API where available, manual entry otherwise.
- **Market condition analysis** — ETFs as the data-collection driver
  (SPY, QQQ, sector ETFs, and their holdings).
- **Opportunity identification** — theme-based tracking of emerging
  opportunities (e.g., SpaceX-adjacent names, drone technology, etc.),
  with markdown research notes authored in iA Writer.
- **Risk management** — position sizing, stops, concentration, and
  "FOMO-guard" style alerts. Initially a stub; rules grow over time.

Explicitly **not** a high-frequency trading tool. The companion **Quant**
project covers ES futures trading.

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  PySide6 UI  (QMainWindow, dockable panes, tab groups)  │
├─────────────────────────────────────────────────────────┤
│  Service layer (pure Python, no Qt imports)             │
│   ├─ SourceRouter     dispatches AV / Schwab / EDGAR    │
│   ├─ WatchlistMgr                                       │
│   ├─ ThemeStore       iA Writer markdown ↔ DB index     │
│   ├─ IndicatorEngine                                    │
│   ├─ RiskEngine       stubs initially                   │
│   └─ Ingestors        prices, fundamentals, news, 8-K   │
├─────────────────────────────────────────────────────────┤
│  Adapters (one per source)                              │
│   alpha_vantage │ schwab │ edgar │ ia_writer            │
├─────────────────────────────────────────────────────────┤
│  Data layer — Mongo models carried from MarketInfo      │
└─────────────────────────────────────────────────────────┘
```

**Hard rule:** the service and data layers contain **zero Qt imports**.
This allows unit testing without a display, notebook scripting against the
DB, and future UI replacement if ever desired.

## 3. Project layout

See [README.md § Project layout](../README.md#project-layout).

## 4. Data sources

### 4.1 Alpha Vantage (primary)

- Paid subscription. API key in `config/secrets.toml` (carried from
  `MarketInfo/utility/secrets.toml`).
- Endpoints used: `TIME_SERIES_DAILY_ADJUSTED`, `OVERVIEW`, `ETF_PROFILE`,
  `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`, `EARNINGS`,
  `NEWS_SENTIMENT`, `LISTING_STATUS`.
- News sentiment subscription confirmed available.

### 4.2 Charles Schwab (secondary + accounts)

- OAuth2 with refresh-token flow. Client ID / secret in `config/secrets.toml`.
- Market data endpoints: `/marketdata/v1/pricehistory`,
  `/marketdata/v1/quotes`, `/marketdata/v1/chains`, `/marketdata/v1/movers`.
- Accounts API: `/accounts`, `/accounts/{id}/positions`.
- **Covers AV coverage gaps** — particularly new IPOs without accumulated
  history. Also supplies option chains (AV doesn't) and account sync.
- **Futures caveat:** the Schwab Trader API covers equities, options, ETFs,
  and mutual funds. Whether futures accounts (legacy TOS / Schwab Futures)
  appear in `/accounts` and return positions via `/positions` must be
  verified against the live API. First smoke test once the key arrives.

### 4.3 SEC EDGAR (via Edgar_Monitor, federated)

- Edgar_Monitor (Node/Vite, existing application) keeps running.
- Edgar_Monitor writes tagged 8-K filings into the shared `MarketAnalysis`
  Mongo DB in the `filings` collection.
- Market Analysis reads; does not re-implement EDGAR fetching.
- Shared tagging vocabulary: a tag in Edgar_Monitor ↔ a watchlist entry
  here, keyed by CIK + ticker.

### 4.4 iA Writer (themes)

- Theme documents live as markdown files in a configurable iA Writer
  folder (iCloud).
- Many documents per theme; documents may be tagged to multiple themes
  (e.g., a SpaceX doc is also tagged `ai` and `social-media`).
- Documents **can be edited in-app or in iA Writer.** The app opens iA
  Writer via `open -a "iA Writer" <path>`; a file-system watcher
  reindexes on save.

### 4.5 Data source routing

`SourceRouter` encapsulates the "coverage-aware" selection rule. UI code
never picks a source directly.

| Need | Primary | Fallback |
| --- | --- | --- |
| Daily prices (seasoned) | AV `TIME_SERIES_DAILY_ADJUSTED` | Schwab `/pricehistory` |
| Daily prices (IPO < ~90d) | **Schwab** `/pricehistory` | AV |
| Intraday / real-time quotes | **Schwab** `/quotes` | AV (delayed) |
| Option chains | **Schwab** `/chains` | — |
| Fundamentals (snapshot) | AV `OVERVIEW` | Schwab fundamental |
| Financial statements | AV | — |
| ETF holdings | AV `ETF_PROFILE` | — |
| News + sentiment | AV `NEWS_SENTIMENT` | (Finnhub later if thin) |
| 8-K filings | Edgar_Monitor → Mongo | — |
| Tradability probe | **Schwab** `/quotes` | — |
| Accounts / positions | Schwab Accounts API (where available); manual otherwise | — |

The router logs every decision so the UI can surface the provenance of
each cell ("source: Schwab fallback — AV returned empty").

## 5. Data model

### 5.1 Database cutover

The existing `MarketAnalysis` MongoDB (built by the MarketInfo prototype)
is being renamed to `MarketAnalysis_Prototype`. A fresh `MarketAnalysis`
is created; collections `daily_quotes` and `indicators` are **copied
forward** from the prototype (expensive to rebuild); everything else is
reingested.

Migration is automated by `scripts/migrate_from_prototype.py`
(idempotent, reports counts).

### 5.2 Collections

| Collection | Source | Purpose |
| --- | --- | --- |
| `companies` | reingest | Fundamentals, ETF membership |
| `etf` | reingest | ETF profile + holdings + sectors |
| `daily_quotes` | **copied forward** | OHLCV, split/dividend adjusted |
| `indicators` | **copied forward** | EMA, RSI, etc. precomputed |
| `theme_groups` | new | Top-level theme categories |
| `themes` | new | Named themes (members of one or more groups) |
| `theme_documents` | new | Markdown files from iA Writer, indexed |
| `watchlist` | new | Tickers + theme tags + ingest tags |
| `accounts` | new (seeded) | Brokerage accounts (api or manual) |
| `positions` | new | Current position snapshots per account |
| `account_sync_log` | new | Audit trail of every sync |
| `filings` | new | 8-K stream from Edgar_Monitor |
| `news` | new | AV news + sentiment |
| `pipeline_definitions` | reviewed | Survivors from prototype |
| `schema_version` | new | Drives migrations |

### 5.3 Theme model

Two-level structure; both levels are tags, multi-valued everywhere.

```
theme_groups:  technology, aerospace, geopolitics, ai, consumer, energy, ...
themes:        spacex, drones, robotics, cybersecurity, nuclear, ...
```

- A **theme** belongs to one or more **groups**. `spacex` ∈ {aerospace, technology}.
- A **document** tags one or more **themes**; group membership is inherited.
- **Unfiled documents** (no `themes:` front-matter) surface in an "Unfiled"
  bin in the Theme tab for later classification.
- **Home doc convention** (optional): a doc with `home: true` renders as
  the theme's landing view.

Markdown front-matter example:

```yaml
---
title: SpaceX prep — post-Starship flight 9
themes: [spacex, ai, social-media]
tickers: [RKLB, ASTS, IRDM]
type: analysis         # analysis | news-clip | rationale | post-mortem
date: 2026-04-18
home: false
---
```

### 5.4 Accounts model

Seeded with three Schwab accounts at migration time:

| Nickname | Broker | Tax type | Access mode | Notes |
| --- | --- | --- | --- | --- |
| Schwab IRA | schwab | ira | api | Trader API |
| Schwab Joint | schwab | joint | api | Trader API |
| Schwab Futures | schwab | futures | api (tentative) | Verify on key arrival |

Non-Schwab accounts: **manual** at the outset.
`scripts/import_positions.py` + a paste-a-table panel in the Portfolio tab.

Current-position snapshots only. **Transactions deferred.**

### 5.5 Ingest tags

Per-ticker opt-ins for optional data streams. Keeps AV call budget bounded.

```
watchlist entry:
  symbol: RKLB
  themes: [spacex]
  ingest_tags: [news_sentiment, options_chain, earnings_history]
```

Current tags (flat namespace):

- `news_sentiment` — AV `NEWS_SENTIMENT`
- `options_chain` — Schwab `/chains`
- `earnings_history` — AV `EARNINGS`
- `financials` — AV `INCOME_STATEMENT` / `BALANCE_SHEET` / `CASH_FLOW`
- `intraday` — Schwab quotes polled during market hours
- `filings_8k` — implicit for any tagged ticker; via Edgar_Monitor

Themes can declare tags that propagate to their tickers as a convenience.

## 6. Background poller

One daily run, **market close + 2 hours** (~18:00 ET on trading days).
Hosted in a QThread. Jobs, in order:

1. Trading-day check (skip weekends/holidays).
2. Price refresh: watchlist + ETF holdings (AV → Schwab fallback).
3. Indicator recompute (incremental).
4. Schwab positions sync (all API accounts).
5. Fundamentals refresh (weekly Fridays; no-op otherwise).
6. Opt-in streams fan-out (tagged tickers only).
7. Filings delta from Edgar_Monitor (tagged CIKs).
8. Theme-doc reconciliation (event-driven by watcher; reconcile here).
9. Summary → `account_sync_log` + UI notification.

All jobs triggerable manually from the Admin tab.

## 7. UI structure

`QMainWindow` + `QTabWidget` + `QDockWidget` side panels (watchlist,
theme list). Tabs:

- **Market** — ETF grid, sector heatmap, breadth, top movers.
- **Theme** — groups ▸ themes ▸ theme view (home doc + document list +
  tickers + correlations + filings), plus the Unfiled bin.
- **Company** — ticker picker, chart (PyQtGraph), fundamentals, indicators,
  filings, news.
- **Portfolio** — positions aggregated across accounts; drill into
  single-account views.
- **Watchlist** — master list with tags, coverage source, last update.
- **News** — AV sentiment feed filtered by watchlist / theme.
- **Filings** — 8-K stream from Edgar_Monitor.
- **Admin** — poller controls, ingest log, source health, DB stats,
  risk-rule editor (future).

Plotting: **PyQtGraph** for interactive/price/indicator charts;
**matplotlib** (via `FigureCanvasQTAgg`) for publication-quality output.

## 8. Build phases

### Phase 0 — _Scaffolding_ ✅ in progress (Ticket 1.0)

Directory layout, `pyproject.toml`, `.gitignore`, documentation folder,
README, module skeletons with docstrings, launcher.

### Phase 1 — Data layer + AV ingest

See Section 9 for ticket breakdown.

### Phase 2 — UI for what we have

Company tab + Watchlist tab against real data. First useful charts.

### Phase 3 — Poller + Market tab

Background scheduler, system tray, Market tab with the ETF grid.

### Phase 4 — Schwab adapter + coverage router

OAuth flow, `/pricehistory` + `/quotes` + `/chains`, `SourceRouter`
wiring, IPO tradability probe, Accounts API sync.

**OAuth callback requirements.** Schwab enforces unusual callback
rules ([app callback URL requirements][schwab-cb]):

- **HTTPS only** — plain `http://` is rejected, even for loopback.
- **Port required** in practice — omitting it means 443, which the OS
  won't bind for a non-root process. Community standard:
  `https://127.0.0.1:8182`.
- **Exact match**, case- and trailing-slash-sensitive. The redirect URI
  we send must match the portal registration byte-for-byte. The #1
  failure in forum reports is a trailing-slash mismatch.
- **30-second response window.** The callback server must accept and
  respond before Schwab times out the authorization redirect.
- **Self-signed certificate** required, because CAs don't issue certs
  for loopback addresses. Must be trusted in macOS Keychain once so
  the browser doesn't block the redirect.
- **255-character total limit** across all registered callback URLs.
- **Refresh-token TTL: 7 days.** Re-authorization is an occasional
  browser-based step, not a daily one.

**Implementation.** Use [`schwab-py`][schwab-py] as the auth and HTTP
layer (cert generation, callback server, token persistence, refresh
handled for us), wrapped by our own thin adapter in
`market_analysis/sources/schwab.py` so services/UI see a typed
interface and the library can be swapped if ever needed.

**Portal registration checklist** — when the app is registered on the
Schwab developer portal, the redirect URI entered there must **exactly
match** the value in `config/settings.toml`
(`schwab.redirect_uri = "https://127.0.0.1:8182"` — no trailing slash,
lowercase scheme).

**Futures smoke test.** First action after a successful token exchange:
call `/accounts` and inspect the response. Confirm whether the Schwab
Futures account appears alongside IRA and Joint. If yes, call
`/accounts/{id}/positions` to verify futures positions surface through
the same endpoint. If no, downgrade the Schwab Futures `accounts`
record to `access_mode: manual` (see ADR-0005).

[schwab-cb]: https://developer.schwab.com/user-guides/apis-and-apps/app-callback-url-requirements
[schwab-py]: https://schwab-py.readthedocs.io/

### Phase 5 — Themes + iA Writer

`themes` / `theme_groups` / `theme_documents` collections, iA Writer
folder watcher, Theme tab, Unfiled bin. Seed with SpaceX / drones themes.

### Phase 6 — News + Filings

AV `NEWS_SENTIMENT` ingest, News tab. Edgar_Monitor Mongo bridge,
Filings tab.

### Phase 7 — Risk & portfolio

Position sizing and stop rules (editable JSON/TOML initially).
Concentration reports. FOMO-guard alerts.

## 9. Phase 1 — ticket breakdown

### Ticket 1.0 — Scaffold ✅

Package layout, deps, entrypoint, documentation folder, README.

### Ticket 1.1 — Config + secrets loader ✅

`config/settings.toml` + `config/secrets.toml` (gitignored).
`services/config.py` returns a cached, typed Pydantic `Settings`
object, merging secrets over settings. Secrets are masked via
`Settings.redacted()` for safe logging. Placeholder window shows
the loaded summary; unit tests in `tests/test_config.py`.

### Ticket 1.2 — Mongo bootstrap + migration script ✅

`data/mongo.py`: connection (`client`, `ping`), typed accessors for
every collection, `TIMESERIES_OPTIONS` for `daily_quotes` and
`indicators`.
`scripts/migrate_from_prototype.py` — idempotent copy of
`daily_quotes` and `indicators` as time-series collections
(`timeField=date, metaField=metadata`, 1-day buckets); creates the
remaining collections with secondary indexes; writes
`schema_version = 1`.  Final counts: 2,891,647 quotes + 514,998
indicators forwarded from the prototype.  See ADR-0008 for the
`metaField` correction.

### Ticket 1.3 — Data models (port from MarketInfo) ✅

Pydantic v2 models under `market_analysis/data/models/` with two base
classes: `MongoModel` (regular collections, `extra="ignore"`) and
`TimeseriesModel` (`extra="allow"` so indicator payloads like
`short`/`middle`/`long` and `value` pass through).  Both expose
`to_mongo()` which strips `_id` when `None`.  Models: `Company`,
`ETF` + `Holding`, `Quote` + `QuoteMeta`, `Indicator` +
`IndicatorMeta`, `ThemeGroup` / `Theme` / `ThemeDocument`,
`WatchlistEntry`, `Account` / `Position` / `AccountSyncLog` (with
`AccessMode` / `TaxType` / `SyncMode` / `SyncStatus` Literals),
`Filing`, `NewsItem`.  Mongo-side `$jsonSchema` validators
intentionally deferred — ingest layer enforces shape on the write
path; adding Mongo validators would couple us to a schema-dialect
translation not worth the effort until schemas stabilize.
`scripts/seed_accounts.py` idempotently upserts the three Schwab
accounts on `nickname`, and is now invoked from the tail of
`migrate_from_prototype.py` so `--force` produces a complete state.
`tests/test_models.py` covers construction, alias roundtrip, `_id`
stripping, time-series extras, and Literal validation.

### Ticket 1.4 — Alpha Vantage adapter ✅

`market_analysis/sources/alpha_vantage.py` — `AlphaVantageClient`
with typed methods `time_series_daily_adjusted`, `overview`,
`etf_profile`, `earnings`, `news_sentiment` (joins tickers/topics
iterables to the AV comma format).  Sliding-window `_RateLimiter`
(default 150/min from settings, configurable per client; thread-safe
belt-and-suspenders).  Optional on-disk cache at `.cache/av/`
(sharded by hash prefix); cache key excludes the API key so rotations
don't blow away replays.  Detects AV's 200-OK error envelopes
(`Error Message`, `Information`, `Note`) and raises
`AlphaVantageError` — but only when they are the dominant payload key,
since AV occasionally returns `Information` alongside real data.  No
DB access (adapter layer is persistence-free).  `tests/test_alpha_vantage.py`
stubs `requests.Session.get` with a `MagicMock` and covers param
shaping, envelope detection, rate-limit blocking, and cache
round-trip.

### Ticket 1.5 — SPY end-to-end ingest ✅

**`services/ingestors/prices.py`** — `ingest_prices(symbol)` fetches
`TIME_SERIES_DAILY_ADJUSTED`, normalizes to `daily_quotes` docs
(`metadata: {symbol, source}`, OHLCV + adjusted_close + dividend +
split + `candle = close - open` for prototype parity), and refreshes
by `delete_many({metadata.symbol})` + `insert_many`.  Time-series
collections don't support unique indexes, so full-refresh is the
idempotency strategy for Phase 1.

**`services/ingestors/etf.py`** — `ingest_etf(symbol)` calls
`ETF_PROFILE`, upserts one `etf` doc (holdings + sector weights),
and `$addToSet`-pushes the ETF symbol onto each holding's
`companies.etf_memberships`.

**`services/indicators.py`** — Pure-math `ema` (α=2/(N+1), SMA-seeded)
and Wilder-smoothed `rsi`.  `EMAStack(short=12, middle=26, long=50)`
writes `short`/`middle`/`long` legs; `RSISpec(period=14)` writes
`value`.  Refresh strategy mirrors prices: delete
`{symbol, indicator, stack}` then insert.  `stack=0` is the Phase 1
default; legacy prototype stacks (1/3/5/9/10) are left untouched
pending a decision on which to maintain.

**`scripts/ingest_etf.py SPY`** — orchestrates profile → prices →
indicators.  Flags: `--limit N`, `--skip-prices`, `--skip-indicators`,
`--include-etf-prices`, `--cache`.

Smoke test (`SPY --limit 3`): 500 holdings upserted; NVDA/AAPL/MSFT
each ingested with 6,657 daily bars (1999-11-01 → 2026-04-21) and
EMA+RSI (6,646 / 6,643 docs each) in ~30s total.  Unit tests
(`tests/test_indicators.py`, `tests/test_ingestors.py`) cover the
pure-math paths and payload normalization; Mongo writes are
exercised by the CLI.  **Total test suite: 50 passing.**

### Ticket 1.6 — Minimum viable UI ✅

**`services/queries.py`** — read-only facade between UI and Mongo.
Tabs never touch `data/` or `sources/` directly (architectural
invariant from § 2).  Helpers return dataclasses / plain dicts:
`db_health()`, `list_symbols_with_quotes()`, `load_quotes()`,
`load_ema_stack()`, `load_company()`, `load_etf()`.

**`app/main_window.py`** — `QMainWindow` hosting a `QTabWidget` with
Admin + Company tabs, File/View/Help menus (Quit ⌘Q, Refresh F5),
and a live status bar (schema version + collection counts).  The
Admin tab's `ingest_finished` signal triggers a Company-tab symbol
refresh so new holdings appear without a manual reload.

**`app/tabs/admin_tab.py`** — Mongo health panel, credential
indicators, per-collection count table, and a manual-ingest form
(symbol / limit / skip-prices / skip-indicators / cache) that
dispatches to an `IngestWorker` on a `QThread` and streams log
lines into a `QPlainTextEdit`.

**`app/tabs/company_tab.py`** — filterable symbol list on the left;
on the right, a PyQtGraph plot (dark theme, `DateAxisItem`) with
adjusted-close + EMA short/middle/long overlays, a fundamentals
`QFormLayout`, and a raw-JSON pane showing the trimmed
`companies` / `etf` docs.

**`app/widgets/ingest_worker.py`** — `QObject` subclass that wraps
the Phase-1 pipeline (`ingest_etf` → `ingest_prices` → indicator
recompute), emitting `log(str)` per step and `done(bool, str)` on
completion.  Lives in `widgets/` so it can be reused by the poller
panel in a later phase.

**Exit criterion — met.** Offscreen boot test: both tabs render;
status bar reports `MarketAnalysis [schema v1]  quotes: 2,892,079
indicators: 554,713   companies: 499`; selecting `A` draws a chart
with 6,500 bars (1999-11-18 → 2025-09-23).

`tests/test_app_imports.py` added — imports every UI module to
catch syntax / circular-import regressions without spinning up Qt.
**Total test suite: 51 passing.**

**Phase 1 exit criteria:** launch app → Admin shows DB healthy →
"Ingest SPY" button → open Company tab → pick a holding like AAPL →
see chart with indicators + fundamentals panel populated.

### Ticket 1.7 — Incremental daily ops + chart polish ✅

Driven by the Phase 1 review ([docs/phase_1_question_period.md](phase_1_question_period.md)).

**AV rate limit.**  Default lowered 150 → 75 req/min (actual paid-tier
ceiling).  Set in both `config/settings.toml` and the `AlphaVantageSettings`
default in `services/config.py`.

**Incremental price refresh.**  `ingest_prices(symbol, mode=…)` now
accepts `"auto"` (default) and `"full"`.  In auto mode it looks up
`max(date)` for the symbol in `daily_quotes`; if present, fetches AV's
100-bar `compact` window and inserts only rows with `date > last` (no
deletes, no duplicates since time-series collections lack unique
indexes — the in-memory filter is the guard).  If the compact window's
oldest bar is still newer than `last + 1`, the call escalates to a full
refresh.  Empty state bootstraps with a full pull.

**Incremental indicators.**  `recompute_for_symbol(symbol, mode=…)` now
supports `"auto"` and `"full"`.  Auto path: compute EMA/RSI over the
full series (reads are cheap; this keeps RSI state exact) and
`insert_many` only docs newer than the last stored date for each
`(indicator, stack)` pair — no deletes.

**Daily orchestrator.**  New `services/ingestors/daily.py`
(`daily_update(...)`):

1. Refresh each selected ETF's profile (one `ETF_PROFILE` call each).
2. **Union-dedupe** holdings across every refreshed ETF so AAPL (in
   SPY + QQQ + XLK) incurs exactly one price call and one indicator
   recompute per cycle.
3. `ingest_prices(..., mode="auto")` per symbol.
4. `recompute_for_symbol(..., mode="auto")` per symbol.

CLI wrapper `scripts/daily_update.py` (all tracked ETFs by default, or
a listed subset); Admin tab exposes the same pipeline.

**Admin tab rework.**  Two sections:
- **Daily update** — ETF scope dropdown ("All tracked ETFs" or any
  individual tracked ETF) + skip/cache options; drives `DailyWorker`.
- **Full refresh (test protocol)** — previous single-ETF delete-and-
  reload path, renamed and kept for seeding / repair.

**Company chart.**  Stacked panels via
`pg.GraphicsLayoutWidget`: price panel (adjusted close + EMA short /
middle / long in distinct high-contrast colors — cyan/lime/amber/pink
on a dark canvas) with a linked RSI panel below (0-100, 30/70 guides,
violet trace).  A toggle row above the chart hides/shows each trace
independently; redraws are cheap (series cached per symbol).
`queries.load_rsi()` added.

`tests/test_incremental.py` — seven tests covering the bootstrap,
incremental append, up-to-date no-op, and gap-escalation price paths
plus the two indicator auto-mode paths.  Fake-collection stubs avoid
a live Mongo.

## 10. Open items / blockers

| Item | Status |
| --- | --- |
| Schwab API credentials | received (in `config/secrets.toml`); Phase 4 unblocked |
| Edgar_Monitor Mongo bridge design | deferred — resolve at Phase 6 |
| Futures account behavior in Schwab Trader API | verify at Phase 4 (see § 8) |
| AI-assisted theme suggestion for Unfiled docs | nice-to-have, post-v1 |

## 11. Revision log

- **v0.5** (2026-04-22) — Ticket 1.7 closed: incremental daily ops
  (price + indicator append-only with gap escalation), union-deduped
  daily orchestrator across tracked ETFs, Admin tab split into
  Daily / Full-refresh sections, Company chart rebuilt with distinct
  EMA colors + linked RSI panel + per-trace toggles, AV default rate
  limit corrected to 75 req/min.
- **v0.4** (2026-04-22) — Phase 4 expanded with Schwab OAuth callback
  requirements (HTTPS-only, exact-match, self-signed cert, 30s window,
  7-day refresh TTL), `schwab-py` recommended as auth layer, futures
  smoke test captured. Schwab credentials received; Phase 4 no longer
  blocked.
- **v0.3** (2026-04-21) — Theme groups; multi-theme per doc; three Schwab
  account types; futures caveat; manual non-Schwab; current positions only.
- **v0.2** (2026-04-21) — Theme = many docs per theme; multi-account
  portfolio with mixed API/manual access; risk stub; tagged/optional ingest;
  poller cadence tightened to "market close + 2h".
- **v0.1** (2026-04-21) — Initial plan: architecture, project layout,
  DB cutover, phased build order, open questions.
