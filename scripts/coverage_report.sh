#!/usr/bin/env bash
# Coverage audit for src/. Level C shipped broken because nothing called it, so
# coverage is treated as a first-class signal here rather than a vanity metric.
#
#   ./scripts/coverage_report.sh          # report
#   ./scripts/coverage_report.sh 75       # report and fail under a floor
set -euo pipefail
cd "$(dirname "$0")/.."
FLOOR="${1:-}"
PY=".venv/bin/python3"
[ -x "$PY" ] || PY="python3"

if ! PYTHONPATH=src "$PY" -c "import pytest_cov" 2>/dev/null; then
  echo "pytest-cov is not installed; run: pip install -r requirements.txt" >&2
  exit 2
fi

ARGS=(-m pytest tests/ -q --cov=src --cov-report=term)
[ -n "$FLOOR" ] && ARGS+=("--cov-fail-under=$FLOOR")
PYTHONPATH=src "$PY" "${ARGS[@]}"
