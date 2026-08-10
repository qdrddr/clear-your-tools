#!/usr/bin/env bash
# Run pytest with coverage on unit + quality_metrics suites.
#
# Usage:
#   ./scripts/local/tests/pytest-coverage.sh [pytest args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${ROOT}"

exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
	--cov=src/cyt \
	--cov-report=term-missing \
	src/tests/unit \
	src/tests/coverage \
	src/tests/quality_metrics \
	"$@"
