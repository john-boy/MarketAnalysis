#!/usr/bin/env bash
# Launch the Market Analysis app with the MLX (Apple GPU) array backend.
# Requires ``mlx`` to be installed in the venv; if it isn't, the backend
# silently falls back to NumPy (see backend.py).  Apple Silicon only.
set -euo pipefail
cd "$(dirname "$0")"
export MARKET_ANALYSIS_BACKEND=mlx
exec .venv/bin/python -m market_analysis.app.main "$@"
