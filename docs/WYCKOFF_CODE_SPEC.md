# WyckoffDB Migration — Claude Code Project Specification
# =========================================================
# Provide this document to Claude Code at the start of each work session.
# It contains all architectural decisions, schemas, and task instructions
# needed to update the MarketAnalysis program and transition to WyckoffDB.
#
# Last updated: 2026-05-09

---

## PROJECT CONTEXT

The existing MarketAnalysis program acquires daily OHLCV data from AlphaVantage,
stores it in a local MongoDB database (also called MarketAnalysis), and performs
several support operations. The program was developed in Claude Code.

We are transitioning to a new database called WyckoffDB designed to support
Wyckoff market cycle analysis. The goals are:

1. Update the existing ingestion code to compute adjusted OHLCV fields at
   ingest time and write to WyckoffDB instead of MarketAnalysis.
2. Migrate all historical and reference data from MarketAnalysis to WyckoffDB.
3. Add new Wyckoff-specific collections to WyckoffDB.
4. When migration is verified complete, delete the MarketAnalysis database.
5. End state: one database (WyckoffDB), one ingestion program (updated
   MarketAnalysis program), no parallel systems.

DO NOT build a new standalone ingestion program. Modify the existing one.

---

## ALPHAVANTAGE DATA — CRITICAL CONTEXT

AlphaVantage TIME_SERIES_DAILY_ADJUSTED returns:
  - open, high, low, close, volume   ← RAW, unadjusted
  - adjusted_close                   ← split + dividend adjusted  ✓
  - split_coefficient                ← 1.0 on non-split days
  - dividend_amount                  ← 0.0 on non-dividend days

The raw OHLCV fields are NOT adjusted for historical splits. For a stock like
AAPL which has split 4:1 (2020), 7:1 (2014), 2:1 (2005), 2:1 (2000), pre-2000
raw prices are 112x larger than their split-adjusted equivalent. This makes raw
OHLCV useless for any technical analysis crossing split boundaries.

THE FIX: Compute adjusted fields at ingest time using this formula:

    adj_factor = adjusted_close / close       # = 1.0 on non-split days
    adj_open   = open   * adj_factor
    adj_high   = high   * adj_factor
    adj_low    = low    * adj_factor
    adj_close  = adjusted_close               # already correct from AV
    adj_volume = volume / adj_factor          # inverse: more shares post-split

Edge cases:
  - If close == 0 or adjusted_close == 0: set adj_factor = 1.0
  - For indices (VIX etc.) that have no volume or adjusted_close: adj_factor = 1.0,
    adj_* fields = raw values (or None where raw is None)
  - adj_factor is always stored even if 1.0, for query consistency

---

## WYCKOFFDB — COMPLETE DATABASE SCHEMA

### Database name: WyckoffDB

---

### Collection: price_history
Type: MongoDB Time Series Collection (TSC)
Purpose: Replaces MarketAnalysis.daily_quotes

TSC creation parameters:
  timeField:             "date"
  metaField:             "metadata"
  granularity:           "hours"
  bucketMaxSpanSeconds:  86400
  bucketRoundingSeconds: 86400

Document schema:
  date              DateTime    [timeField — required]
  metadata          Object      [metaField — required]
    .symbol         String      ticker symbol e.g. "AAPL"
    .source         String      "alpha_vantage"
    .asset_type     String      "equity" | "etf" | "index"

  # Raw fields from AlphaVantage (store as-is)
  open              Float       raw unadjusted open
  high              Float       raw unadjusted high
  low               Float       raw unadjusted low
  close             Float       raw unadjusted close
  volume            Float       raw unadjusted volume (None for indices)
  adjusted_close    Float       AV adjusted close (None for indices)
  split_coefficient Float       AV split coefficient (None for indices)
  dividend          Float       AV dividend amount (None for indices)

  # Computed at ingest — NEVER retrofitted after insert
  adj_factor        Float       adjusted_close / close  (8 decimal places)
  adj_open          Float       open * adj_factor       (6 decimal places)
  adj_high          Float       high * adj_factor       (6 decimal places)
  adj_low           Float       low  * adj_factor       (6 decimal places)
  adj_close         Float       == adjusted_close       (6 decimal places)
  adj_volume        Integer     volume / adj_factor     (rounded to int)
  candle            Float       close - open            (pre-computed body)
  adj_candle        Float       adj_close - adj_open

Indexes:
  [metadata.symbol ASC, date ASC]    — primary time-series lookup
  [date DESC]                        — recency queries / daily updates

Notes:
  - TSC documents are immutable after insertion. All fields must be correct
    at insert time. Never attempt $set updates on measurement fields.
  - To re-ingest a date range: delete the date range for that symbol first,
    then insert fresh. TSC supports deletes in MongoDB 5.1+.
  - Upsert pattern: delete matching {metadata.symbol, date range} then insert.

---

### Collection: companies
Type: Regular collection
Purpose: Company reference data — migrate unchanged from MarketAnalysis.companies

Document schema: preserve all existing fields, add none.
  symbol          String    [unique index]
  name            String
  exchange        String
  sector          String
  industry        String
  country         String
  currency        String
  description     String
  fiscal_year_end String
  fundamentals    Object    (nested financial ratios)
  etf_memberships Array
  last_updated    DateTime

Indexes:
  symbol ASC  UNIQUE

---

### Collection: etf
Type: Regular collection
Purpose: ETF reference data — migrate unchanged from MarketAnalysis.etf

Document schema: preserve all existing fields.
  symbol          String    [unique index]
  name            String
  provider        String
  asset_class     String
  description     String
  expense_ratio   Float
  holdings        Array
  sector_weights  Object
  last_updated    DateTime

Indexes:
  symbol ASC  UNIQUE

---

### Collection: watchlist
Type: Regular collection
Purpose: Symbols under active monitoring — migrate from MarketAnalysis.watchlist,
         add two new fields.

Document schema: preserve all existing fields, add:
  symbol              String    [unique index]
  added_at            DateTime
  last_updated        DateTime
  notes               String
  source_of_origin    String
  ingest_tags         Array
  themes              Array
  wyckoff_priority    Integer   NEW — 1 (highest) to 5 (lowest), default 3
  last_phase_checked  DateTime  NEW — when Wyckoff phase was last evaluated

Indexes:
  symbol ASC  UNIQUE

Migration note: set wyckoff_priority=3, last_phase_checked=None for all
migrated documents.

---

### Collection: splits_events
Type: Regular collection
Purpose: Historical split event log per symbol, sourced from AV SPLITS endpoint.
         Used to verify adj_factor computation and for analytical reference.

Document schema:
  symbol            String    ticker
  effective_date    DateTime  date split took effect
  split_factor      Float     e.g. 4.0 for a 4:1 split
  cumulative_factor Float     product of all split factors up to this date
  ingested_at       DateTime  when this record was written

Indexes:
  [symbol ASC, effective_date ASC]  UNIQUE

Population: fetch AV SPLITS endpoint for each symbol on initial backfill.
Not needed on daily updates (splits are rare events).

---

### Collection: features
Type: MongoDB Time Series Collection (TSC)  — OR regular collection
      (Use TSC if append-only workflow is confirmed; regular if labels need
       to be revised. Recommend regular for flexibility during development.)
Purpose: Computed Wyckoff feature vectors per symbol per date.
         Input to HMM phase classifier.

Document schema:
  symbol          String    ticker
  date            DateTime
  feature_set     String    version tag e.g. "wyckoff_v1"

  # Price-based features
  atr_14          Float     Average True Range, 14-period
  atr_ratio       Float     ATR / close  (normalized)
  hurst_100       Float     Hurst exponent, 100-bar window  (0.5=random, >0.5=trending)
  bb_width        Float     Bollinger Band width  (upper-lower)/middle
  bb_position     Float     (close - lower) / (upper - lower)  0..1
  hl_range_ratio  Float     (high - low) / close

  # Volume-based features (use adj_volume)
  obv             Float     On Balance Volume (running)
  obv_slope_20    Float     OBV linear regression slope, 20-bar
  cmf_20          Float     Chaikin Money Flow, 20-period
  vol_price_corr  Float     rolling 20-bar correlation of adj_volume and adj_close
  vol_ratio_20    Float     adj_volume / 20-bar avg adj_volume

  # Structure features (use adj_* prices)
  swing_high_20   Float     highest adj_high in 20 bars
  swing_low_20    Float     lowest  adj_low  in 20 bars
  sr_distance     Float     distance to nearest support/resistance level
  above_vwap      Boolean   adj_close > VWAP for the period

  model_version   String    feature set version that produced this record
  computed_at     DateTime  when this record was written

Indexes:
  [symbol ASC, date ASC]              UNIQUE per symbol per date
  [symbol ASC, feature_set ASC, date ASC]

Notes: All price-based features MUST use adj_* fields, not raw fields.
This collection is populated by the feature engineering pipeline (future phase).

---

### Collection: phase_labels
Type: Regular collection (updateable — labels may be revised by analyst or model)
Purpose: Wyckoff phase classification per symbol per date.

Document schema:
  symbol            String    ticker
  date              DateTime
  phase             String    "Accumulation" | "Markup" | "Distribution" | "Markdown"
  sub_phase         String    "A"|"B"|"C"|"D"|"E" within Accumulation/Distribution
  phase_probability Object    {accumulation, markup, distribution, markdown}
                              four floats summing to 1.0
  model_version     String    which HMM version produced this label
  labeled_at        DateTime  when label was computed
  is_manual         Boolean   True if analyst overrode the model label
  manual_note       String    reason for manual override (if applicable)

Indexes:
  [symbol ASC, date ASC]        UNIQUE
  [symbol ASC, phase ASC]       for phase-filtered queries
  [phase ASC, date DESC]        for cross-symbol phase queries

---

### Collection: transitions
Type: Regular collection
Purpose: Detected Wyckoff phase transition events.

Document schema:
  symbol            String    ticker
  detection_date    DateTime  when the transition was detected
  from_phase        String    phase being left
  to_phase          String    phase being entered
  cusum_statistic   Float     CUSUM value at detection
  confidence        Float     model confidence 0..1
  confirmed_date    DateTime  date transition was confirmed retrospectively
  is_confirmed      Boolean   True after confirmation window passes
  model_version     String

Indexes:
  [symbol ASC, detection_date DESC]
  [symbol ASC, is_confirmed ASC]

---

### Collection: projections
Type: Regular collection
Purpose: Price and duration projections per symbol per phase.
         Updated at each analysis session.

Document schema:
  symbol            String    ticker
  projection_date   DateTime  date this projection was generated
  current_phase     String    Wyckoff phase at time of projection
  price_target_q10  Float     10th percentile price target
  price_target_q50  Float     50th percentile price target (median)
  price_target_q90  Float     90th percentile price target
  duration_days_q10 Integer   10th percentile estimated days remaining in phase
  duration_days_q50 Integer   50th percentile
  duration_days_q90 Integer   90th percentile
  model_version     String
  created_at        DateTime

Indexes:
  [symbol ASC, projection_date DESC]
  [symbol ASC, current_phase ASC, projection_date DESC]

---

### Collections to NOT migrate
The following MarketAnalysis collections are NOT migrated to WyckoffDB.
They are either empty, deprecated, or replaced by the new schema:

  account_sync_log    — empty, operational log not needed
  accounts            — portfolio/broker accounts; out of scope for Wyckoff
  extractors          — ingestion job definitions; logic moves into updated program
  filings             — empty
  indexes             — 4 market index definitions; recreate manually in watchlist
  indicators          — 6.3M computed indicators; will be recomputed by feature
                        engineering pipeline using adj_* data; do not migrate
  news                — empty
  pipeline_definitions — empty
  positions           — empty; out of scope
  schema_version      — replaced by application version management
  theme_documents, theme_groups, themes — empty
  system.buckets.*    — internal MongoDB TSC storage; not directly migrated
  system.views        — MongoDB views; recreate if needed after migration

---

## CHANGES TO THE EXISTING INGESTION PROGRAM

### Change 1 — Database connection retarget
Find: all MongoClient connections pointing to "MarketAnalysis"
Change to: "WyckoffDB"
Note: There may be a config file, constants module, or .env variable.
      Update at the source, not in every call site.

### Change 2 — Collection name mapping
Old name              →  New name
daily_quotes          →  price_history
companies             →  companies          (unchanged)
etf                   →  etf                (unchanged)
watchlist             →  watchlist          (unchanged)
indicators            →  features           (schema change — see Change 4)

### Change 3 — Add adj_* computation to daily quote ingestion
Find the function that assembles the document dict before inserting into
daily_quotes (now price_history). Add the following block immediately before
the database insert call. This is the ONLY location where this computation
should exist — do not duplicate it.

```python
def _compute_adj_fields(doc: dict) -> dict:
    """
    Compute split-adjusted OHLCV fields.
    adj_factor = 1.0 on non-split days (values unchanged).
    adj_factor < 1.0 on pre-split history (prices scale down).
    Call this once per document, immediately before insert.
    """
    close   = doc.get("close")   or 0.0
    adj_cl  = doc.get("adjusted_close") or close
    factor  = (adj_cl / close) if close != 0.0 else 1.0

    def _ap(v):   # adjusted price
        return round(v * factor, 6) if v is not None else None
    def _av(v):   # adjusted volume (inverse)
        return round(v / factor) if v is not None and factor != 0.0 else None

    raw_open = doc.get("open")

    return {
        "adj_factor":  round(factor, 8),
        "adj_open":    _ap(raw_open),
        "adj_high":    _ap(doc.get("high")),
        "adj_low":     _ap(doc.get("low")),
        "adj_close":   round(float(adj_cl), 6),
        "adj_volume":  _av(doc.get("volume")),
        "candle":      round(float(doc.get("close", 0)) - float(raw_open or 0), 6),
        "adj_candle":  round(float(adj_cl) - float(_ap(raw_open) or 0), 6),
    }
```

Usage at the insert site:
```python
doc.update(_compute_adj_fields(doc))
# then insert doc into price_history
```

### Change 4 — metadata field: add asset_type
When building the metadata sub-document for price_history, add:
  "asset_type": "equity" | "etf" | "index"

Determine asset_type from context:
  - If the symbol is in the watchlist with ingest_tags containing "index": "index"
  - If the symbol is in the etf collection: "etf"
  - Otherwise: "equity"

### Change 5 — Daily update idempotency pattern
The existing program's daily update must handle the TSC upsert constraint.
TSC does not support upsert. Replace any upsert logic with:
  Step 1: delete documents where metadata.symbol == symbol AND
          date >= earliest_date_in_batch AND date <= latest_date_in_batch
  Step 2: insert_many the new batch

This is safe and idempotent. It handles re-runs and partial failures correctly.

### Change 6 — Add splits_events population (backfill only)
Add a one-time backfill function that fetches AV SPLITS for each symbol
and populates splits_events. This does NOT run on daily updates.
Trigger it manually after the migration is complete.
See splits_events schema above for document structure.

### Change 7 — Remove indicators ingestion or redirect to features
The existing program computes and stores indicators into MarketAnalysis.indicators.
Decision:
  - DO NOT migrate the 6.3M existing indicator documents. They were computed
    on unadjusted price data and are therefore unreliable for Wyckoff analysis.
  - If the existing program's indicator computation code is still needed
    operationally (non-Wyckoff use), refactor it to write to a separate
    collection and clearly label it as non-adjusted.
  - The Wyckoff features collection will be populated separately by the
    feature engineering pipeline (future development phase).
  - For now: disable or comment out the indicators ingestion to WyckoffDB.
    Do not delete the code — it may be useful as reference for feature
    engineering implementation.

---

## MIGRATION TASK LIST FOR CLAUDE CODE

Implement in this order. Each task should be independently testable.

### TASK 1 — Create WyckoffDB collections and indexes
File to create or modify: database setup module (or create wyckoff_db_setup.py)

Steps:
  a. Create price_history as TSC with parameters above
  b. Create or verify: companies, etf, watchlist (regular)
  c. Create: splits_events, features, phase_labels, transitions, projections (regular)
  d. Build all indexes per schema above
  e. Write a verification function that lists all collections and their
     document counts and index names

Completion check: running the verification function shows all 9 collections
with correct indexes and zero documents (except any already migrated).

### TASK 2 — Migrate reference collections
Source: MarketAnalysis  →  Target: WyckoffDB

  a. companies: insert_many from source (drop _id, re-insert)
  b. etf: insert_many from source
  c. watchlist: insert_many from source, set wyckoff_priority=3 and
     last_phase_checked=None on all documents

Completion check: document counts match between source and target for
companies and etf. Watchlist count matches. Spot-check 3 documents each.

### TASK 3 — Migrate price_history from daily_quotes
Source: MarketAnalysis.daily_quotes  →  Target: WyckoffDB.price_history

Steps:
  a. Read documents from daily_quotes in batches of 1000
  b. For each document:
     - Extract symbol from metadata.symbol
     - Compute adj_* fields using _compute_adj_fields()
     - Set metadata.asset_type appropriately
     - Build new document per price_history schema
  c. Insert batch into price_history
  d. Log progress every 100,000 documents

Completion check:
  - WyckoffDB.price_history document count == MarketAnalysis.daily_quotes count
  - Query AAPL records from around 2020-08-28 (just before the 4:1 split).
    Verify adj_factor ≈ 0.25 and adj_open ≈ open * 0.25
  - Query AAPL records from 2026-05-08 (current, no split pending).
    Verify adj_factor == 1.0 and adj_open == open

### TASK 4 — Update ingestion to write to WyckoffDB
Modify: the existing daily ingestion functions

Steps:
  a. Update database name from "MarketAnalysis" to "WyckoffDB"
  b. Update collection name from "daily_quotes" to "price_history"
  c. Insert _compute_adj_fields() call before every document insert
  d. Replace upsert logic with delete-then-insert per Change 5
  e. Add asset_type to metadata per Change 4
  f. Test with one symbol (AAPL) in isolation before running full watchlist

Completion check:
  - Ingest AAPL compact (100 bars) to WyckoffDB
  - Verify 100 new/updated documents in price_history
  - Verify adj_factor, adj_open, adj_high, adj_low, adj_volume are all
    present and non-null on each document

### TASK 5 — Run full ingestion cycle
  a. Run the updated daily ingestion for all watchlist symbols
  b. Review error log — investigate any symbols with > 5% failure rate
  c. Confirm total document count in price_history is reasonable
     (expected: 3,191,125 from migration + net new from ingestion run)

### TASK 6 — Populate splits_events (one-time backfill)
  a. For each symbol in watchlist where asset_type == "equity":
     fetch AV SPLITS endpoint and populate splits_events
  b. Respect AV rate limits (12.5 seconds between requests on free tier,
     1.0 second on premium)

### TASK 7 — Verification before cutover
Run these checks. All must pass before MarketAnalysis is deleted.

  a. price_history count >= daily_quotes count
  b. AAPL adj_factor = 0.25 for dates 2020-08-28 and earlier (pre-4:1 split)
  c. AAPL adj_factor = 1.0 for 2026-05-08 (current)
  d. All watchlist symbols present in price_history
  e. companies count matches
  f. etf count matches
  g. watchlist count matches and wyckoff_priority field present on all docs
  h. Daily ingestion runs successfully and writes to WyckoffDB

### TASK 8 — Cutover and cleanup
  a. Confirm all Task 7 checks pass
  b. Drop database: client.drop_database("MarketAnalysis")
  c. Remove any remaining references to "MarketAnalysis" or "daily_quotes"
     in the codebase
  d. Run a final grep/search for "MarketAnalysis" and "daily_quotes" in
     all .py files to confirm no references remain

---

## PRESERVED FUNCTIONALITY

The following existing support routines must be preserved and continue to work
after retargeting to WyckoffDB. Do not delete or disable them.

  - Watchlist management (add/remove symbols, update tags and themes)
  - Company metadata fetch and update (from AV COMPANY_OVERVIEW or equivalent)
  - ETF profile fetch and update (from AV ETF_PROFILE)
  - Daily ingestion scheduler / trigger logic
  - Error logging and retry logic
  - Rate limit handling for AlphaVantage API
  - Any backfill utility for fetching full historical data for a new symbol

These routines require only the database/collection retargeting from Tasks 1–4.
Their internal logic does not need to change.

---

## FUTURE PHASES (do not implement now)

The following are planned but out of scope for this migration:

  - Feature engineering pipeline (populates features collection)
  - HMM phase classifier (populates phase_labels)
  - CUSUM/BOCPD transition detector (populates transitions)
  - Quantile regression projection model (populates projections)
  - Backtesting framework

These are documented here so Code understands the purpose of the new
collections and does not modify or delete them during the migration.

---

## ENVIRONMENT

  Runtime:    Python 3.11, conda environment 'wyckoff'
  IDE:        PyCharm on MacBook Pro M2
  MongoDB:    Local instance, port 27017
  AV API key: stored in environment variable ALPHAVANTAGE_API_KEY
  Project:    /Users/jhenry/Workspaces/GitHub/PyCharm/WyckoffPrelims/

---

## SESSION STARTUP INSTRUCTION FOR CLAUDE CODE

At the start of each Code session, read this document completely before
making any changes. After reading, summarize:
  1. Which task you are starting on
  2. Which files you plan to modify
  3. What the completion check for this task is
Then proceed with implementation.
