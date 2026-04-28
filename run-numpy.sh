#!/usr/bin/env bash
# Launch the Market Analysis app with the NumPy (CPU) array backend.
# This is the default behavior; the explicit env var makes intent visible
# in the logs and guards against a stray shell export flipping the switch.
set -euo pipefail
cd "$(dirname "$0")"
export MARKET_ANALYSIS_BACKEND=numpy
exec .venv/bin/python -m market_analysis.app.main "$@"
