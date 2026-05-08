#!/usr/bin/env bash
# Run every saved extractor and write each output to extracts/<name>_<YYYYMMDD>.csv.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m scripts.run_extractor --all "$@"
