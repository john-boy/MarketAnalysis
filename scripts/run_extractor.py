"""CLI: run a saved Extractor and write the dataframe to CSV.

Usage::

    python -m scripts.run_extractor my_xlf_relstrength
    python -m scripts.run_extractor sector_rotation -o extracts/run.csv

Lists available extractors with ``--list``.
"""

from __future__ import annotations

import argparse
import logging
import sys

from market_analysis.services import extractors as ex


log = logging.getLogger("run_extractor")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", nargs="?", help="Extractor name to run.")
    ap.add_argument("-o", "--output", help="Output CSV path (default: extracts/<name>_<ts>.csv).")
    ap.add_argument("--list", action="store_true", help="List available extractors and exit.")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list:
        rows = ex.list_extractors()
        if not rows:
            print("(no extractors defined)")
            return 0
        for rec in rows:
            target = rec.etf_symbol if rec.kind == "etf" else f"{len(rec.symbols)} symbol(s)"
            print(f"  {rec.name:<32}  {rec.kind:<8}  {target}  since {rec.start_date.date()}")
        return 0

    if not args.name:
        ap.error("name is required (or pass --list)")

    try:
        rows, report = ex.extract(args.name, progress=print)
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    out = ex.write_csv(rows, args.output or ex.default_output_path(args.name))
    print(
        f"Wrote {report.total_rows:,} rows across {len(report.symbols)} symbol(s) to {out}."
    )
    if report.missing_symbols:
        print(f"WARNING: no data for: {', '.join(report.missing_symbols)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
