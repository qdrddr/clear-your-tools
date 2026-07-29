#!/usr/bin/env bash
# Run one Python test category (separate prek hooks per type).
#
# Usage:
#   ./scripts/local/tests/pytest-category.sh unit|gherkin-unit|quality_metrics|coverage|mutation|qa
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${ROOT}"

category="${1:?usage: $0 unit|gherkin-unit|quality_metrics|coverage|mutation|qa}"

case "${category}" in
unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/unit \
		--ignore=src/tests/unit/gherkin \
		-m "not integration and not gherkin and not qa" \
		"${@:2}"
	;;
gherkin-unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/unit/gherkin \
		-m gherkin \
		"${@:2}"
	;;
quality_metrics)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/quality_metrics \
		"${@:2}"
	;;
coverage)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/coverage \
		"${@:2}"
	;;
mutation)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/mutation \
		"${@:2}"
	;;
qa)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/qa \
		-m qa \
		--run-qa \
		"${@:2}"
	;;
*)
	echo "unknown category: ${category} (expected unit|gherkin-unit|quality_metrics|coverage|mutation|qa)" >&2
	exit 1
	;;
esac
