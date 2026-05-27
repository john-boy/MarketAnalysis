# Change Request: 2026-05-12

The ETFs often change holdings.  During the daily update please read each ETF, determine whether there are any changes to the holdings list, add any new holdings if the new holding does not already exist, delete any dropped holdings if the dropped holding is not referenced in another ETF.

When adding an ETF allow name and description to be entered.

Provide an update for ETFs including name and description.


Plan locked. Implementing now in this order: services first (etf.py + daily.py), then UI (admin_tab.py + company_tab.py). I'll also add small tests where the logic warrants.

Now the daily orchestrator: surface reconciliation stats and include orphaned companies in the union.

Now the Admin tab — Description column on the ETF table, and the Add form gets Name + Description fields.

Now update the table refresh + cell-change handler + add-click to handle Description.

Now update `_on_etf_add_clicked` to write user-supplied name/description to the (yet-to-exist) doc before the worker runs. We pre-seed an `etf` doc with `$setOnInsert` so the ingestor's user-override-protection kicks in.

Good. Now the Company tab — add a "Delete symbol" button.

Now add the click handler. Place it near `_on_selection_changed`.

Tidying the duplicate import.

Compile / smoke test.

The pyqtgraph failure is environmental (not in test env), not caused by my changes. Confirming by running just the service tests + ingestor logic tests:

All changes compile and the 37 service-layer tests pass. The lone test failure (`test_app_imports.py`) is the pre-existing `pyqtgraph` not in the test env — unrelated to these edits.

## Summary of changes

**`market_analysis/services/ingestors/etf.py`** — [view](market_analysis/services/ingestors/etf.py)
- `ingest_etf()` now reads the existing doc first, computes dropped holdings (`prev_holdings - new_holdings`), and `$pull`s the ETF symbol from each dropped company's `etf_memberships`. The company row itself is preserved.
- User-supplied `name` and `description` are protected: if the stored doc already has a non-empty value, AV cannot overwrite it.
- New: `set_etf_description()` (mirrors `set_etf_name`), `delete_company()` (companies row + price_history + indicators, plus defensive scrubs), `list_orphaned_companies()`.
- `ETFIngestReport` gains `dropped_symbols` and `memberships_pulled`.

**`market_analysis/services/ingestors/daily.py`** — [view](market_analysis/services/ingestors/daily.py)
- Orphaned companies (empty/missing `etf_memberships`) are folded into the union, so their price + indicator updates continue until you delete them.
- Per-ETF log line now shows dropped count + first 5 symbols.
- `DailyReport` gains `holdings_dropped`, `memberships_pulled`, `orphaned_companies`; final tally includes them.

**`market_analysis/app/tabs/admin_tab.py`** — [view](market_analysis/app/tabs/admin_tab.py)
- ETF table is now 3 columns (Symbol / **Name** / **Description**), both right-most editable in place.
- Add-ETF form expanded with Name + Description inputs. User values get `$set` upserted into the `etf` doc *before* the ingest worker runs; the ingestor's override-protection then prevents AV from clobbering them.
- `_on_etf_cell_changed` dispatches to the right setter based on column.

**`market_analysis/app/tabs/company_tab.py`** — [view](market_analysis/app/tabs/company_tab.py)
- "Delete symbol…" button in the header next to the symbol label.
- Confirm dialog shows current `etf_memberships` and warns if the symbol is itself a tracked ETF (directs user to the ETFs tab instead).
- On confirm: full delete + symbol-list refresh.

Things to validate against your real Mongo:
1. **Reconciliation**: run `python -m scripts.daily_update <one ETF>` and check the log for "dropped N: …" if anything has changed since last run.
2. **User override survival**: set a description in the table, run daily update, confirm the description didn't get clobbered.
3. **Orphan flow**: drop a holding from an ETF, run daily update, confirm orphan appears in the union (look for the "+ N orphaned companies …" line).
4. **Company delete UI**: pick an orphan, click Delete symbol, confirm it's gone from the list.