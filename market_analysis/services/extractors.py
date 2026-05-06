"""Extractor service — persisted ML-feed dataframe specifications.

Two kinds of extractors:

- ``etf`` — relative-strength: SPY + the ETF + every current
  constituent stock of that ETF.
- ``rotation`` — sector-rotation: SPY + a user-supplied list of ETFs
  (or arbitrary tickers).

Output is a flat row-oriented sequence of dicts (one row per
``(symbol, date)`` pair) carrying the columns required by the ML
pipeline::

    date, volume, low, open, close, adjusted_close, high, candle, symbol

Each row also carries the ``extractor`` name so multiple extracts can
land in a single file or table without losing provenance.

The service intentionally avoids a hard dependency on pandas: rows
are plain dicts and the CSV writer in :func:`write_csv` uses the
stdlib.  Callers that want a DataFrame can pass the rows to
``pandas.DataFrame(...)`` themselves.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from market_analysis.data import mongo
from market_analysis.data.models import Extractor, utcnow


SPY = "SPY"

# The order of columns in the output, per the change request spec.
COLUMNS: tuple[str, ...] = (
    "date",
    "volume",
    "low",
    "open",
    "close",
    "adjusted_close",
    "high",
    "candle",
    "symbol",
)


# -- CRUD -----------------------------------------------------------------


def list_extractors() -> list[Extractor]:
    return [
        Extractor.model_validate(doc)
        for doc in mongo.extractors().find({}).sort("name", 1)
    ]


def get_extractor(name: str) -> Extractor | None:
    doc = mongo.extractors().find_one({"name": name})
    return Extractor.model_validate(doc) if doc else None


def save_extractor(rec: Extractor) -> bool:
    """Upsert an Extractor by ``name``.  Returns True iff a new doc was inserted."""
    rec.last_updated = utcnow()
    doc = rec.to_mongo()
    res = mongo.extractors().update_one(
        {"name": rec.name},
        {"$set": doc},
        upsert=True,
    )
    return res.upserted_id is not None


def delete_extractor(name: str) -> bool:
    return mongo.extractors().delete_one({"name": name}).deleted_count > 0


# -- Symbol resolution ----------------------------------------------------


def resolve_symbols(rec: Extractor) -> list[str]:
    """Return the ordered list of symbols an extractor will pull.

    Order: SPY first, then the ETF (if etf-kind), then the
    constituent stocks (etf-kind) or the user-supplied symbols
    (rotation-kind).  Duplicates are removed while preserving order.
    """
    out: list[str] = [SPY]
    if rec.kind == "etf":
        out.append(rec.etf_symbol or "")
        etf_doc = mongo.etf().find_one({"symbol": rec.etf_symbol}) or {}
        for h in etf_doc.get("holdings") or []:
            sym = h.get("symbol")
            if sym:
                out.append(sym)
    else:  # rotation
        out.extend(rec.symbols)

    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        s = (s or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        deduped.append(s)
    return deduped


# -- Extract --------------------------------------------------------------


@dataclass
class ExtractReport:
    name: str
    kind: str
    start_date: datetime
    symbols: list[str] = field(default_factory=list)
    rows_per_symbol: dict[str, int] = field(default_factory=dict)
    total_rows: int = 0
    missing_symbols: list[str] = field(default_factory=list)


def extract(name: str, *, progress=None) -> tuple[list[dict[str, Any]], ExtractReport]:
    """Build the extraction dataframe rows for the named extractor.

    Returns ``(rows, report)`` — rows is a list of dicts in
    :data:`COLUMNS` order with a leading ``extractor`` field.  The
    report carries per-symbol bar counts and any symbols that returned
    no data (so the UI/CLI can flag coverage gaps).
    """
    rec = get_extractor(name)
    if rec is None:
        raise KeyError(f"Extractor not found: {name}")

    symbols = resolve_symbols(rec)
    if progress:
        progress(f"resolved {len(symbols)} symbol(s) for {name}: {', '.join(symbols)}")

    report = ExtractReport(
        name=rec.name,
        kind=rec.kind,
        start_date=rec.start_date,
        symbols=symbols,
    )

    rows: list[dict[str, Any]] = []
    coll = mongo.daily_quotes()
    proj = {
        "_id": 0,
        "date": 1,
        "open": 1,
        "high": 1,
        "low": 1,
        "close": 1,
        "adjusted_close": 1,
        "volume": 1,
        "candle": 1,
        "metadata.symbol": 1,
    }

    for sym in symbols:
        cur = coll.find(
            {"metadata.symbol": sym, "date": {"$gte": rec.start_date}},
            projection=proj,
        ).sort("date", 1)
        n = 0
        for doc in cur:
            rows.append({
                "extractor": rec.name,
                "date": doc.get("date"),
                "volume": doc.get("volume"),
                "low": doc.get("low"),
                "open": doc.get("open"),
                "close": doc.get("close"),
                "adjusted_close": doc.get("adjusted_close"),
                "high": doc.get("high"),
                "candle": doc.get("candle"),
                "symbol": sym,
            })
            n += 1
        report.rows_per_symbol[sym] = n
        if n == 0:
            report.missing_symbols.append(sym)
        if progress:
            progress(f"  {sym}: {n:,} rows")

    report.total_rows = len(rows)

    mongo.extractors().update_one(
        {"name": rec.name},
        {"$set": {"last_run": utcnow()}},
    )

    return rows, report


# -- Output ---------------------------------------------------------------


def write_csv(rows: Iterable[dict[str, Any]], path: str | Path) -> Path:
    """Write extracted rows to ``path`` as CSV.  Returns the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["extractor", *COLUMNS]
    with p.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            out = {k: row.get(k) for k in fieldnames}
            d = out.get("date")
            if isinstance(d, datetime):
                out["date"] = d.isoformat()
            w.writerow(out)
    return p


def default_output_path(name: str, *, root: str | Path = "extracts") -> Path:
    """Return ``<root>/<name>_<YYYYMMDD-HHMMSS>.csv`` for an extract."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return Path(root) / f"{safe}_{ts}.csv"
