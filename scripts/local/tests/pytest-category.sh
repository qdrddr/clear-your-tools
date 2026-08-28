#!/usr/bin/env bash
# Run one Python test category (separate prek hooks per type).
#
# Usage:
#   ./scripts/local/tests/pytest-category.sh unit|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${ROOT}"

category="${1:?usage: $0 unit|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime}"

ensure_native_import() {
	if uv run python -c "import cyt_indexer._native" 2>/dev/null; then
		return 0
	fi
	bash "${ROOT}/scripts/local/dev/maturin-develop.sh"
}

ensure_native_import

case "${category}" in
unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/unit \
		--ignore=src/tests/unit/gherkin \
		-m "not integration and not gherkin and not qa and not runtime" \
		"${@:2}"
	;;
gherkin-unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/unit/gherkin \
		-m "gherkin and not runtime" \
		"${@:2}"
	;;
quality_metrics)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/quality_metrics \
		-m "not runtime" \
		"${@:2}"
	;;
coverage)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/coverage \
		-m "not runtime" \
		"${@:2}"
	;;
mutation)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/mutation \
		-m "not runtime" \
		"${@:2}"
	;;
qa)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/qa \
		-m qa \
		--run-qa \
		"${@:2}"
	;;
runtime)
	exec env CYT_RUN_RUNTIME_TESTS=1 uv run pytest \
		src/tests/runtime \
		-m runtime \
		--run-runtime \
		"${@:2}"
	;;
*)
	echo "unknown category: ${category} (expected unit|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime)" >&2
	exit 1
	;;
esac
