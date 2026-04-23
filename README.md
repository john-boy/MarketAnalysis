# Market Analysis

A comprehensive desktop application for portfolio management, market condition
analysis, opportunity identification, and risk management. Aggregates and
correlates information from multiple sources (Alpha Vantage, Charles Schwab,
SEC EDGAR) to identify and exploit market opportunities — with a strong bias
toward **theme-based** investing.

> Unlike the companion **Quant** project (focused on ES trading), Market
> Analysis is a broad-spectrum data collection and analysis tool intended to
> understand and detect themes and theme-based opportunities in the overall
> market.

## Status

**Phase 1 — Scaffolding.** Package layout and documentation in place.
Data-layer port from the `MarketInfo` prototype and first end-to-end ETF
ingest (SPY) are next.

See [`docs/plan.md`](docs/plan.md) for the full rolling build plan and
[`docs/decisions.md`](docs/decisions.md) for the architectural decision log.

## Tech stack

- **UI:** PySide6 (Qt 6) — desktop app, native look, dockable panes
- **Charts:** PyQtGraph (interactive), matplotlib (publication)
- **Data store:** MongoDB (collection: `MarketAnalysis`)
- **Scheduler:** APScheduler, hosted in a QThread
- **Language:** Python 3.12+

## Data sources

| Source | Role |
| --- | --- |
| **Alpha Vantage** | Primary — prices, fundamentals, ETF holdings, news & sentiment |
| **Charles Schwab** | Gap-fill prices for new IPOs, option chains, intraday quotes, account/position sync |
| **SEC EDGAR** | 8-K filings for tagged companies, federated from the Edgar_Monitor application |
| **iA Writer** | Markdown theme documents (iCloud folder), watched for changes |

## Project layout

```
MarketAnalysis/
├── main.py                    # launcher (delegates to market_analysis.app.main)
├── pyproject.toml
├── docs/                      # plan, decisions, design notes
├── market_analysis/
│   ├── app/                   # PySide6 UI (tabs, widgets, main window)
│   ├── services/              # business logic — no Qt imports
│   ├── sources/               # external API adapters (AV, Schwab, EDGAR, iA)
│   └── data/                  # Mongo models, schemas, migrations
├── config/                    # settings.toml, secrets.toml (gitignored)
├── scripts/                   # CLI entry points (migrate, ingest, backfill)
└── tests/
```

## Quickstart (development)

```bash
# 1. Create / activate venv (PyCharm does this automatically)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install in editable mode with dev extras
pip install -e '.[dev]'

# 3. Copy the secrets template and fill in your API keys
cp config/secrets.toml.example config/secrets.toml
# edit config/secrets.toml

# 4. Run
python main.py
```

## Related projects

- **Edgar_Monitor** — SEC EDGAR filing monitor (Node/Vite); federated into
  this application via a shared Mongo `filings` collection.
- **MarketInfo** — Streamlit-based prototype that built the original
  `MarketAnalysis` Mongo database. Retained for reference; not the
  foundation for this application.
- **Quant** — companion project focused on futures (ES) trading.

## License

Private. Greenthread Companies, LLC.
