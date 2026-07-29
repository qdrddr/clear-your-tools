#!/usr/bin/env bash
# Run one Python test category (separate prek hooks per type).
#
# Usage:
#   ./scripts/pytest-category.sh unit|gherkin-unit|quality_metrics
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT}"

category="${1:?usage: $0 unit|gherkin-unit|quality_metrics}"

case "${category}" in
unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS uv run pytest \
		src/tests/unit \
		--ignore=src/tests/unit/gherkin \
		-m "not integration and not gherkin" \
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
*)
	echo "unknown category: ${category} (expected unit|gherkin-unit|quality_metrics)" >&2
	exit 1
	;;
esac
