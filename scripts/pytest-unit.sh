#!/usr/bin/env bash
# Run unit tests only (excludes @pytest.mark.integration).
#
# Integration tests call real external APIs and are intended for manual runs:
#   uv run pytest -m integration --run-integration
#
# Usage:
#   ./scripts/pytest-unit.sh [pytest args...]
#   ./scripts/pytest-unit.sh src/tests/unit/test_foo.py -k pattern
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

DEFAULT_PATHS=(src/tests/unit src/tests/quality_metrics)
if [ "$#" -eq 0 ]; then
	set -- "${DEFAULT_PATHS[@]}"
fi

exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest -m "not integration" "$@"
