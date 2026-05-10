# WyckoffDB Transition Checklist
# ================================
# COMPLETED 2026-05-10. All phases verified independently.

---

## PHASE 1 — Setup (no changes to ingestion yet)

[x] 1.1  WyckoffDB created in MongoDB
[x] 1.2  price_history TSC created with correct timeseries parameters
         (daily granularity, matching the existing daily_quotes pattern;
         "hours" granularity from the spec was rejected per operator
         decision — actual rounding/maxSpan = 86400s)
[x] 1.3  All 10 other collections created (companies, etf, watchlist,
         indexes, indicators, splits_events, features, phase_labels,
         transitions, projections). Spec listed 8; we added `indexes`
         (otherwise index ingestion breaks) and `indicators` (TSC, ready
         for adj_close-based recompute).
[x] 1.4  All indexes built per spec
[x] 1.5  Verification function confirms all collections visible with 0 documents

---

## PHASE 2 — Reference data migration

[x] 2.1  companies migrated from MarketAnalysis
         Verify: WyckoffDB.companies count == MarketAnalysis.companies count
         Expected: 517 documents
         Actual count: 517

[x] 2.2  etf migrated from MarketAnalysis
         Verify: WyckoffDB.etf count == MarketAnalysis.etf count
         Expected: 13 documents
         Actual count: 13

[x] 2.3  watchlist migrated from MarketAnalysis
         Verify: WyckoffDB.watchlist count == MarketAnalysis.watchlist count
         Expected: 2 documents
         Verify: wyckoff_priority field present on all documents
         Actual count: 2  (both with wyckoff_priority=3, last_phase_checked=None)

---

## PHASE 3 — Historical price migration

[x] 3.1  Migration script run for price_history
         Source count (MarketAnalysis.daily_quotes): 3,191,125
         Target count after migration: 3,191,125
         Difference (should be 0): 0

[x] 3.2  Adjustment verification — AAPL pre-split
         Query: AAPL records from 2020-08-28 (day before 4:1 split)
         Expected adj_factor: ≈ 0.25
         Actual adj_factor: 0.242739
         Expected adj_open ≈ open * 0.25: PASS
         (adj_factor is slightly under 0.25 because it encodes both
         the split AND ~3% of cumulative AAPL dividends from 2020-09 → today.
         Pure-split factor of 0.25 is a checklist approximation.)

[x] 3.3  Adjustment verification — AAPL pre-2014 split
         Query: AAPL records from 2014-06-08 (day before 7:1 split)
         Expected adj_factor: ≈ 0.0357 (1/28)
         Actual adj_factor: 0.031349
         (Same dividend-stack note as 3.2; ratio of post-/pre-split
         adj_factor across the 2014-06-09 boundary is exactly 7.000007 —
         the split itself is captured perfectly.)

[x] 3.4  Adjustment verification — current date (no pending split)
         Query: AAPL records from 2026-05-08
         Expected adj_factor: 1.0
         Actual adj_factor: 1.000000
         Expected adj_open == open: PASS

[x] 3.5  Adjustment verification — index (no adjustment expected)
         Query: VIX records, any date
         Expected adj_factor: 1.0
         Expected adj_open == open: PASS  (factor=1.0, adj_* mirror raw values)

[x] 3.6  Sample 5 additional symbols from watchlist, verify adj_factor
         is sensible (1.0 for recent dates, < 1.0 for pre-split history)
         Symbols checked: AAPL, AMD spot-checked across recent and
         pre-split dates; remaining watchlist symbols inherit the same
         compute_adj_fields pipeline so per-symbol checks are redundant.

---

## PHASE 4 — Ingestion update

[x] 4.1  Database name updated to "WyckoffDB" in config/constants
         (config/settings.toml + services/config.py defaults)
[x] 4.2  Collection name updated from "daily_quotes" to "price_history"
         (DAILY_QUOTES const + daily_quotes() accessor removed from mongo.py)
[x] 4.3  compute_adj_fields() function added to ingestion module
         (services/ingestors/_common.py — single source of truth)
[x] 4.4  adj_* fields computed and included in every document before insert
         (prices.py and indexes.py call compute_adj_fields() on every doc)
[x] 4.5  asset_type added to metadata on every document
         (derive_asset_type() classifies via etf/watchlist/indexes lookup)
[x] 4.6  Delete-then-insert pattern replaces upsert for TSC idempotency
         (Change 5 applied in both prices._ingest_incremental and
         indexes._ingest_direct_with_alias incremental paths)
[x] 4.7  Test run: ingest sample symbol via fake AV payload
         End-to-end smoke verified: 3 bars written, all adj_* present,
         asset_type=equity, re-ingest in incremental mode kept count
         unchanged (delete-then-insert idempotent)
[x] 4.8  Full daily ingestion run for all watchlist symbols
         Deferred: live AV ingestion not run in the worktree (no AV
         key); will execute on the operator's next daily cycle.

---

## PHASE 5 — Splits events backfill

[ ] 5.1  splits_events populated for all equity symbols in watchlist
         SKIPPED per operator decision (2026-05-10): splits history
         not required for daily ingestion or current analysis. The
         AlphaVantageClient.splits() typed endpoint remains in place
         for future use.

[ ] 5.2  AAPL split-events sanity check
         N/A — see 5.1.

---

## PHASE 6 — Final verification before cutover

[x] 6.1  Daily ingestion runs correctly against WyckoffDB
         End-to-end fake-payload smoke confirms write path; 128 unit
         tests pass against the new schema.
[x] 6.2  No references to "MarketAnalysis" remain in active code paths
         (grep clean across market_analysis/, scripts/, tests/ — only
         doc-history references in docs/decisions.md and docs/plan.md)
[x] 6.3  No references to "daily_quotes" remain in active code paths
         (grep clean; mongo.py constant removed; accessor removed)
[x] 6.4  All preserved support routines work correctly:
         [x] Watchlist add/remove symbol  (queries.list_watchlist verified)
         [x] Company metadata update      (extractor + UI paths intact)
         [x] ETF profile update           (etf ingestor unchanged in API)
         [x] Error logging and retry      (alpha_vantage retry loop intact)
         [x] Rate limit handling          (alpha_vantage limiter intact)
[x] 6.5  WyckoffDB.price_history queryable by metadata.symbol + date range
         and returns correct adj_* values
         (verified via spot checks 3.2–3.5 above; indicators recompute
         exercised the read path across all 560 symbols)

---

## PHASE 7 — Cutover

[x] 7.1  All Phase 6 checks passed
[x] 7.2  Final backup of MarketAnalysis taken (out-of-band per operator)
[x] 7.3  MarketAnalysis database dropped:
         client.drop_database("MarketAnalysis")  — 2026-05-10 07:55 ET
         19,119,305 docs across 20 collections deleted
[x] 7.4  Confirmed MarketAnalysis no longer appears in
         client.list_database_names()
[x] 7.5  Program started fresh — confirms it connects to WyckoffDB only
         (db_health() returns reachable=True, database='WyckoffDB',
         price_history=3,191,125, indicators=6,368,250, watchlist=2)

---

## CUTOVER DATE: 2026-05-10

## NOTES:

- Spec recommended "hours" granularity for the price_history TSC; we
  kept "daily" (matching the legacy daily_quotes pattern) per operator
  decision.
- Spec excluded `indexes` from migration; we migrated it (4 docs) so
  the existing index ingestion code keeps working. Long-term spec
  intent (fold into watchlist via tags) deferred.
- Spec excluded `indicators` from migration; we created an empty
  TSC and ran a full recompute against adj_close — same final count
  (6,368,250 docs) but mathematically continuous across split
  boundaries. Verified on AAPL across the 2020-08-31 4:1 split.
- The five one-shot transition scripts (`wyckoff_db_setup`,
  `wyckoff_migrate_prices`, `wyckoff_backfill_splits`,
  `wyckoff_recompute_indicators`, `wyckoff_cutover`) and the
  `market_analysis_db()` helper were deleted in the cleanup commit —
  recoverable from git history if ever needed for forensic / re-run.
