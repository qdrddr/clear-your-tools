#!/usr/bin/env bash
# Run unit + quality_metrics app tests (fast local default; excludes gherkin/coverage/mutation/qa).
#
# Integration tests call real external APIs and are intended for manual runs:
#   uv run pytest -m integration --run-integration
#
# Full CI parity (all automated categories):
#   ./scripts/pytest-app-ci.sh
#
# Usage:
#   ./scripts/pytest-unit.sh [pytest args...]
#   ./scripts/pytest-unit.sh src/tests/unit/test_foo.py -k pattern
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

if [ "$#" -gt 0 ]; then
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest -m "not integration and not qa" "$@"
fi

bash "${SCRIPT_DIR}/pytest-category.sh" unit
bash "${SCRIPT_DIR}/pytest-category.sh" quality_metrics
