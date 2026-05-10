# WyckoffDB Transition Checklist
# ================================
# Work through this checklist in order.
# Check off each item only after independently verifying it — not just
# after the code runs without errors.
#
# Keep this file updated as you progress.

---

## PHASE 1 — Setup (no changes to ingestion yet)

[ ] 1.1  WyckoffDB created in MongoDB
[ ] 1.2  price_history TSC created with correct timeseries parameters
[ ] 1.3  All 8 other collections created (companies, etf, watchlist,
         splits_events, features, phase_labels, transitions, projections)
[ ] 1.4  All indexes built per spec
[ ] 1.5  Verification function confirms all collections visible with 0 documents

---

## PHASE 2 — Reference data migration

[ ] 2.1  companies migrated from MarketAnalysis
         Verify: WyckoffDB.companies count == MarketAnalysis.companies count
         Expected: 517 documents
         Actual count: ______

[ ] 2.2  etf migrated from MarketAnalysis
         Verify: WyckoffDB.etf count == MarketAnalysis.etf count
         Expected: 13 documents
         Actual count: ______

[ ] 2.3  watchlist migrated from MarketAnalysis
         Verify: WyckoffDB.watchlist count == MarketAnalysis.watchlist count
         Expected: 2 documents
         Verify: wyckoff_priority field present on all documents
         Actual count: ______

---

## PHASE 3 — Historical price migration

[ ] 3.1  Migration script run for price_history
         Source count (MarketAnalysis.daily_quotes): 3,191,125
         Target count after migration: ______
         Difference (should be 0): ______

[ ] 3.2  Adjustment verification — AAPL pre-split
         Query: AAPL records from 2020-08-28 (day before 4:1 split)
         Expected adj_factor: ≈ 0.25
         Actual adj_factor: ______
         Expected adj_open ≈ open * 0.25: PASS / FAIL

[ ] 3.3  Adjustment verification — AAPL pre-2014 split
         Query: AAPL records from 2014-06-08 (day before 7:1 split)
         Expected adj_factor: ≈ 0.0357 (1/28)
         Actual adj_factor: ______

[ ] 3.4  Adjustment verification — current date (no pending split)
         Query: AAPL records from 2026-05-08
         Expected adj_factor: 1.0
         Actual adj_factor: ______
         Expected adj_open == open: PASS / FAIL

[ ] 3.5  Adjustment verification — index (no adjustment expected)
         Query: VIX records, any date
         Expected adj_factor: 1.0
         Expected adj_open == open: PASS / FAIL

[ ] 3.6  Sample 5 additional symbols from watchlist, verify adj_factor
         is sensible (1.0 for recent dates, < 1.0 for pre-split history)
         Symbols checked: ______________________________

---

## PHASE 4 — Ingestion update

[ ] 4.1  Database name updated to "WyckoffDB" in config/constants
[ ] 4.2  Collection name updated from "daily_quotes" to "price_history"
[ ] 4.3  _compute_adj_fields() function added to ingestion module
[ ] 4.4  adj_* fields computed and included in every document before insert
[ ] 4.5  asset_type added to metadata on every document
[ ] 4.6  Delete-then-insert pattern replaces upsert for TSC idempotency
[ ] 4.7  Test run: ingest AAPL compact (100 bars) to WyckoffDB
         Verify: 100 documents written
         Verify: adj_factor present on all 100
         Verify: adj_open == open * adj_factor on spot checks
[ ] 4.8  Full daily ingestion run for all watchlist symbols
         Symbols attempted: ______
         Symbols succeeded: ______
         Symbols failed: ______ (list: ___________________________)

---

## PHASE 5 — Splits events backfill

[ ] 5.1  splits_events populated for all equity symbols in watchlist
         Total split events stored: ______
[ ] 5.2  Verify AAPL has 4 split events:
         2020-08-31  factor=4.0
         2014-06-09  factor=7.0
         2005-02-28  factor=2.0
         2000-06-21  factor=2.0
         PASS / FAIL

---

## PHASE 6 — Final verification before cutover

[ ] 6.1  All daily ingestion runs correctly against WyckoffDB for 3 consecutive days
[ ] 6.2  No references to "MarketAnalysis" remain in active code paths
         (grep result: 0 matches in .py files)
[ ] 6.3  No references to "daily_quotes" remain in active code paths
[ ] 6.4  All preserved support routines work correctly:
         [ ] Watchlist add/remove symbol
         [ ] Company metadata update
         [ ] ETF profile update
         [ ] Error logging and retry
         [ ] Rate limit handling
[ ] 6.5  WyckoffDB.price_history queryable by metadata.symbol + date range
         and returns correct adj_* values

---

## PHASE 7 — Cutover

[ ] 7.1  All Phase 6 checks passed
[ ] 7.2  Final backup of MarketAnalysis taken (optional but recommended)
         mongodump --db MarketAnalysis --out ~/backups/MarketAnalysis_final
[ ] 7.3  MarketAnalysis database dropped:
         client.drop_database("MarketAnalysis")
[ ] 7.4  Confirmed MarketAnalysis no longer appears in:
         client.list_database_names()
[ ] 7.5  Program started fresh — confirms it connects to WyckoffDB only

---

## CUTOVER DATE: __________________

## NOTES:
