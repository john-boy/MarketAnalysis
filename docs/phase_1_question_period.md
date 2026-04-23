## Admin tab — what each piece does

### The layout

**Left column** — read-only health dashboard:
- **Mongo**: URI / database / reachability / schema version (from the `schema_version` collection).
- **Credentials**: whether `secrets.toml` has an Alpha Vantage key and Schwab client id+secret (actual values never shown).
- **Collections**: `estimated_document_count()` per collection. The "Refresh" button re-queries everything.

**Right column** — the "Ingest ETF" control + log pane.

---

### The workflow when you click "Run"

The form builds an `IngestWorker`, moves it onto a fresh `QThread`, and runs this three-stage pipeline (the same one behind `python -m scripts.ingest_etf`):

**Stage 1 — ETF profile.** Calls Alpha Vantage `ETF_PROFILE` for the symbol (e.g. `SPY`). Writes:
- one doc into `etf` (name, provider, expense ratio, sector weights, full holdings list),
- and one doc per holding into `companies` (symbol + name), `$addToSet`-ing the ETF symbol onto each holding's `etf_memberships`.

For SPY this returns ~500 holdings.

**Stage 2 — Prices.** For each holding symbol, calls AV `TIME_SERIES_DAILY_ADJUSTED` (`outputsize=full` → ~25 years), normalizes each row into a `daily_quotes` doc (`metadata: {symbol, source}`, OHLCV + adjusted_close + dividend + split + `candle = close - open`). Idempotent via `delete_many({metadata.symbol: SYM})` followed by `insert_many` — a full refresh per symbol, because time-series collections can't have unique indexes.

**Stage 3 — Indicators.** For each holding, reads the quote series back from Mongo, computes EMA (12/26/50 → `short`/`middle`/`long`, `stack=0`) and RSI (14, `stack=0`), and refreshes `indicators` docs keyed by `(symbol, indicator, stack)`.

Throughout, the worker emits one log line per step into the text pane. When `done` fires, the status bar updates, the Company tab's symbol list refreshes (so new holdings show up), and the "Run" button re-enables.

---

### The form fields

**Symbol.** The ETF ticker. Anything uppercased; `SPY` is the Phase-1 test subject.

**Limit holdings.** A `QSpinBox` capping how many holdings from Stage 1 go through Stages 2 and 3. `0` shows as "all"; any positive number slices the holdings list (`targets[:limit]`). Purpose: **rate-limit and time management.** SPY is 500 holdings; at ~150 AV requests/minute and ~1 request per symbol for the prices stage, a full SPY ingest is ~3½ minutes just for prices. While iterating on UI code you don't want to re-pull all 500 every test, so `limit=3` keeps the feedback loop under a minute. The ETF-level write (Stage 1) always covers all holdings — the limit only bounds the downstream pulls.

**Options checkboxes:**

- **Skip prices.** Stage 1 runs (ETF + companies get updated), Stage 2 is skipped, Stage 3 still runs against whatever quotes already exist. Useful when you've already pulled fresh prices and just want to re-rebuild indicators (e.g. after tweaking EMA periods).

- **Skip indicators.** Stages 1 and 2 run; Stage 3 is skipped. Useful when you want fresh prices but not the cost of indicator recompute (a few ms per symbol, but adds up at 500 symbols). Also useful when you plan to re-run indicators separately after changing parameters.

- **Use disk cache.** Passed through to `AlphaVantageClient(cache=True)`. On a hit, the AV HTTP call is bypassed and the previous JSON response is loaded from `.cache/av/<hash>.json` (keyed by the request params, not the API key). Useful while debugging the ingest code — you can re-run the pipeline without consuming AV quota. **Default: on.** Turn it off for a genuine refresh from Alpha Vantage; leave it on while iterating on parsing / indicator / UI code.

---

### A couple of sharp edges worth knowing

- There's no **cancel** button yet. If you kick off a full SPY run and want to stop, you have to close the app.
- Idempotency is **full-refresh**: Stage 2 wipes a symbol's quotes before re-inserting. If AV returns a degraded payload (fewer bars than before), you'll temporarily have a shorter history. The delete+insert is ordered, so you won't end up with partial duplicates, but it's not transactional across collections.
- The log pane caps at 5,000 lines (`setMaximumBlockCount`). For a full-SPY run that's fine; if you ever run multiple ETFs back-to-back, older lines scroll off.
- `Limit holdings` also implicitly limits **Stage 3**, so if you run `SPY --limit 3` the first time, only 3 symbols get indicators computed — the other 497 holdings sit in `companies` with no quotes and no indicators. That's expected but worth remembering when you view the Company tab and wonder why `ZTS` shows "no quotes."

Want me to add any of: a Cancel button, a progress bar, a "recompute indicators only" shortcut, a "full SPY" nightly button, or a pre-flight estimate (expected runtime / request count) before the run starts?

# Comments and Questions

It seems the Admin UI is designed for testing.  That's fine.  But I have some comments and questions regarding daily operations.

First be aware that the AlphaVantage rate tier allows 75 requests per minute.

What happens if you exceed the limit?

If you exceed your rate limit, you do not get banned or charged; the API will simply return a 429 Too Many Requests or a standard JSON error message indicating that the standard API call frequency has been exceeded. If you are coding a pipeline on the free tier, it is standard practice to implement a sleep() function of at least 12 seconds between your requests to avoid hitting the per-minute wall.

Please incorporate throttling into the AV stream.

Second, in the prototype application the full quote stream is not reloaded during daily updates.  The application looks at the most recent quote date for each symbol and only loads the missing dates.  In other words from the most recent to the current date.  This is a very efficient procedure.  Please incorporate this into the daily operations.

The full collection is not loaded every update cycle, only on the initial loading.

Third, daily operations should be designed to update all ETF quote streams.  Similarly, I believe the indicator updates are applied from most recent to current.  If this makes sense the daily update procedure is efficient.

Think about the daily updates as incremental procedures.  It this makes sense, please incorporate into the code.

Fourth, the Company UI graph shows only one EMA trace.  The EMA trace colors are difficult to see.  Please adjust the trace colors and produce all indicator traces.  It may be reasonable to toggle the indicator traces;  show EMA (or not), show RSI (or not), etc.

I'm OK with the Admin UI as a test protocol.  Please make the daily operations more automatic as suggested above.  In particular you suggested a "full SPY" button.  I'd prefer to have a selection to refresh all or one selected ETF.  SPY is only one option.  QQQ, XLE, etc. will be loaded.  Remember also that there is considerable overlap among ETFs.  AAPL is in many ETFs.  The quote stream needs updating only once per cycle.

What do you think?
