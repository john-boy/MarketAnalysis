Proceeding with Ticket 1.1. Scope: typed settings loader that merges `config/settings.toml` + `config/secrets.toml`, with a cached accessor and a redacted view safe for logs. I'll also wire it into the placeholder window so you can see it's working, and add a unit test.

All 5 tests pass. Let me also update the plan to mark 1.1 done.

## Ticket 1.1 — complete

### What's new

- **`market_analysis/services/config.py`** — typed settings loader.
  - Pydantic `Settings` with one section per concern (`mongo`, `poller`, `ia_writer`, `alpha_vantage`, `schwab`, `logging`).
  - Merges `config/settings.toml` + `config/secrets.toml` (optional); cached via `@lru_cache`.
  - `Settings.redacted()` masks secret fields (`api_key`, `client_secret`, `refresh_token`) for safe logging/UI display.
  - `has_alpha_vantage_key()` / `has_schwab_credentials()` helpers.
  - `reload_settings()` to pick up on-disk changes (for a future Admin-tab reload button).

- **`tests/test_config.py`** — 5 tests, all passing:
  - loads settings-only (secrets absent)
  - merges secrets overlay
  - redacts correctly (including short-value fallback)
  - raises when `settings.toml` is missing
  - reload picks up file changes

- **Placeholder window updated** to show a redacted config summary so you can see the loader working at a glance:

  ```
  Mongo:          mongodb://localhost:27017 / MarketAnalysis
  Alpha Vantage:  not configured       ← until you create secrets.toml
  Schwab:         not configured       ← pending API key
  iA Writer dir:  (unset)
  Poller:         18:00 America/New_York (enabled)
  ```

### Action for you

**Create `config/secrets.toml`** from the example to light up Alpha Vantage:

```bash
cp config/secrets.toml.example config/secrets.toml
```

Then edit it with the AV key from `MarketInfo/utility/secrets.toml` (`NVJ0935IXWSCLGIV`). The file is gitignored. Relaunch and you'll see `Alpha Vantage: configured`.

### Next

**Ticket 1.2 — Mongo bootstrap + migration script** (connection, collection accessors, `scripts/migrate_from_prototype.py`). Give the word when you're ready.

