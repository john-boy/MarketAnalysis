# Daily Update Issue

## John
The daily batch update does not include the indexes.  This might be a passed argument issue? Please take a look and make the appropriate modifications to include indexes in the daily update.

## Claude
Looking at this, I can see the bug. The service `daily_update()` supports a `skip_indexes` parameter, the GUI worker passes it through, but the CLI script `scripts/daily_update.py` neither exposes the flag nor passes the argument — so on the CLI path, the index step's behavior is whatever the function default happens to be, with no way to control it. More importantly, the docstring and `--skip-indicators` flag give no indication that indexes are part of the pipeline.

Let me update the CLI to expose `--skip-indexes` and pass it through, matching the worker.

Updated [scripts/daily_update.py](scripts/daily_update.py):
- Added `--skip-indexes` argparse flag
- Passes `skip_indexes=args.skip_indexes` through to `daily_svc.daily_update(...)`

Now the CLI mirrors `DailyWorker` (used by the Admin tab) and the index-refresh step runs by default. If you were seeing indexes truly absent on CLI runs, the next likely culprit is empty seed data — run `python -m scripts.seed_indexes` once if `mongo.indexes()` is empty, since `index_ingestor.list_tracked()` returning `[]` will silently print "No tracked indexes."
