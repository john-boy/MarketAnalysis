# Change Requests

This request is to implement an extraction functionality for use in ML models.

The models are of two types.

Type 1.  The model will analyze the relative strength of stocks within an ETF.
What we want to provide to the ML is a dataframe with quotes for the ETF, quotes for each of the constituent stocks,
and quotes for SPY.  This will allow comparative strength analysis against the broad market and the market sector.

Type 2. The model will perform a sector rotation analysis.
What we want to provide to the ML is a datafram with quotes for SPY and quotes for a list of ETFs.
The base example is the set of sector SPDR ETFs; XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', and 'XLY'.

Please provide an administrative display that allows specifications for multiple extractors.  The display will allow
either specification of an ETF implying that the ETF, SPY, and the constituent stocks will be extracted.  The other
type of selection will allow specification of all the items to be extracted.  This accommodates the rotation analysis
type of selection.  Each specification will have a user-supplied name.  Finally, each specification will have a beginning date
which will be the start of the data sequence for each quote series in the dataframe.  Output of the extracted data
will contain the name of the specification.

For each stock or ETF the columns of the dataframe will contain
date, volume, low, open, close, adjusted_close, high, candle, and symbol.

## Claude

Now add the Mongo collection.

Now the service.

Now the CLI script:

Now add the extractors tab builder and handlers. Add before the `_on_fund_batch_clicked` method or after the fundamentals tab. Let me append the extractor tab builder before `_on_fund_batch_clicked`:

Now wire up `_refresh_extractor_table` from `refresh()` and disable extractor buttons during ingest.

All tests pass. The `test_app_imports` failure was a pre-existing environment issue (missing pyqtgraph), unrelated.

Implemented the ML-feed extractor system end-to-end:

**Data layer**
- New [Extractor](market_analysis/data/models/extractor.py) Pydantic model — `kind` ∈ `{etf, rotation}`, with `etf_symbol` (relative-strength) or `symbols` list (rotation), plus `name`, `start_date`, `description`, `last_run`. Validators enforce that the right payload is set per kind.
- New `extractors` collection registered in [mongo.py](market_analysis/data/mongo.py) (`EXTRACTORS`, `extractors()` accessor, included in `ALL_COLLECTIONS` so it shows up in Admin → Mongo counts).

**Service** ([market_analysis/services/extractors.py](market_analysis/services/extractors.py))
- CRUD: `list_extractors`, `get_extractor`, `save_extractor` (upsert by name), `delete_extractor`.
- `resolve_symbols(rec)` — SPY first, then ETF + holdings (etf-kind) or the symbols list (rotation-kind), deduped/uppercased.
- `extract(name)` — pulls `daily_quotes` rows since `start_date`, returns flat list of dicts with the spec's columns (`date, volume, low, open, close, adjusted_close, high, candle, symbol`) plus a leading `extractor` field for provenance, and an `ExtractReport` capturing per-symbol bar counts and missing symbols. Updates `last_run` on the record.
- `write_csv` + `default_output_path` — stdlib-only CSV writer, no pandas dependency added.

**Admin UI** ([market_analysis/app/tabs/admin_tab.py](market_analysis/app/tabs/admin_tab.py))
- New "Extractors" tab: spec table (name/kind/target/start/last-run), edit form with kind-aware stacked input (ETF symbol vs. comma-separated symbols), date picker, Save / New / Run extract / Remove buttons. Selecting a row populates the form for editing. Run extract prompts for an output path, runs synchronously with progress in the existing log pane, and refreshes the table.

**CLI** ([scripts/run_extractor.py](scripts/run_extractor.py))
- `python -m scripts.run_extractor <name>` runs and writes a CSV; `--list` prints saved extractors.

**Tests** ([tests/test_extractors.py](tests/test_extractors.py))
- Model validation (required fields per kind).
- `resolve_symbols` for both kinds, including dedup and SPY-folding.
- `extract` end-to-end with stubbed `mongo.daily_quotes` / `mongo.extractors`.
- CSV writer header + content.

All 23 tests in `test_extractors.py` + `test_models.py` pass. Nothing committed.