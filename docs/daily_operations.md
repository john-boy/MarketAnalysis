# Daily Ingest Operations

Here's the procedure:

**Run the daily update CLI** after the market closes:

```bash
.venv/bin/python -m scripts.daily_update
```

That runs the full pipeline in [scripts/daily_update.py](scripts/daily_update.py) → [market_analysis/services/ingestors/daily.py](market_analysis/services/ingestors/daily.py:66):

1. Refreshes every tracked ETF profile (one `ETF_PROFILE` call each)
2. Unions all holdings into a deduped symbol set (AAPL appears once, even if it's in SPY/QQQ/XLK)
3. Incrementally pulls new daily bars — `ingest_prices(mode='auto')` only fetches dates after the last stored bar
4. Recomputes indicators append-only for each `(indicator, stack)`

**Useful flags:**
- `python -m scripts.daily_update SPY QQQ` — only refresh holdings of specific ETFs
- `--skip-indicators` — prices only, defer indicator recompute
- `--skip-prices` — indicators only (e.g., after a stack change)
- `--limit-symbols 50` — cap the union for a smoke test
- `--log-level DEBUG` — verbose
- Exit code is non-zero if any errors were collected

**Timing:** the docstring at [scripts/daily_update.py:9](scripts/daily_update.py:9) notes it's intended for "market close + 2h" — Alpha Vantage's daily bars settle a bit after 4pm ET, so running ~6pm ET is the safe window.

**From the GUI:** the Admin tab also exposes this same `daily_update` call if you'd rather click than cron it.

Want me to set it up as a scheduled task (launchd or cron) so it runs automatically every weekday evening?

Yes...

I'll set up a launchd agent to run weekdays at 5:00 PM CT (= market close + 1h ET buffer; AV daily bars are settled by then).

Loaded and registered. Summary:

- **Schedule:** Mon–Fri at 5:00 PM CT (= 6:00 PM ET, ~2h after market close)
- **Plist:** `~/Library/LaunchAgents/com.greenthread.marketanalysis.daily.plist`
- **Logs:** `logs/daily_update.log` (stdout) and `logs/daily_update.err` (stderr) in the project root — they append, so rotate occasionally
- **Command run:** `.venv/bin/python -m scripts.daily_update` (no args → all tracked ETFs, prices + indicators)

**Useful commands:**
```bash
# Run it now to verify end-to-end
launchctl start com.greenthread.marketanalysis.daily

# Tail the log
tail -f logs/daily_update.log

# Disable temporarily
launchctl unload ~/Library/LaunchAgents/com.greenthread.marketanalysis.daily.plist

# Re-enable
launchctl load -w ~/Library/LaunchAgents/com.greenthread.marketanalysis.daily.plist
```

**Caveats worth knowing:**
1. launchd only fires when the Mac is awake. If the machine is asleep at 5pm, the job is skipped (it does *not* catch up on wake — that requires `pmset` wake schedules separately).
2. It runs in your user session, so you need to be logged in. If you sometimes log out, switch to a LaunchDaemon under `/Library/LaunchDaemons` instead.
3. AlphaVantage credentials need to be readable from the env launchd gives the job — typically loaded from a `.env` in the working directory; verify after the first scheduled run by checking the err log.

Want me to kick off a manual run now to confirm it works end-to-end?

