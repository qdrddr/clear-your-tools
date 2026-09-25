#!/usr/bin/env bash
# Run one Python test category (separate prek hooks per type).
#
# Usage:
#   ./scripts/local/tests/pytest-category.sh unit|unit-shard INDEX TOTAL|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${ROOT}"

category="${1:?usage: $0 unit|unit-shard INDEX TOTAL|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime}"

# Set by prek-loop / prek-loop-py-parallel for live pytest output.
PYTEST_VERBOSE_ARGS=()
if [[ "${CYT_PREK_VERBOSE_PYTEST:-}${CYT_PREK_PARALLEL_LOG:-}" == *1* ]]; then
	# tee-sys streams lines immediately; conftest emits [timing] per test; durations= slowest at end.
	PYTEST_VERBOSE_ARGS=(-v --capture=tee-sys --durations=25 --durations-min=0.1)
fi

PYTEST_XDIST_ARGS=()
if [[ "${CYT_PYTEST_XDIST:-}" == 1 ]]; then
	PYTEST_XDIST_ARGS=(-n auto)
fi

ensure_native_import() {
	if uv run python -c "import cyt_indexer._native" 2>/dev/null; then
		return 0
	fi
	bash "${ROOT}/scripts/local/dev/maturin-develop.sh"
}

_run_unit_pytest() {
	local -a targets=("$@")
	if ((${#targets[@]} == 0)); then
		echo "pytest unit shard has no test files" >&2
		return 0
	fi
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_QA_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		"${targets[@]}" \
		--ignore=src/tests/integration \
		-m "not integration and not gherkin and not qa and not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}"
}

ensure_native_import

case "${category}" in
unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_QA_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/unit \
		--ignore=src/tests/unit/gherkin \
		--ignore=src/tests/integration \
		-m "not integration and not gherkin and not qa and not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
unit-shard)
	shard_index="${2:?usage: $0 unit-shard INDEX TOTAL}"
	shard_total="${3:?usage: $0 unit-shard INDEX TOTAL}"
	mapfile -t SHARD_FILES < <(
		uv run python "${SCRIPT_DIR}/pytest-unit-shard.py" --shard "${shard_index}" --shards "${shard_total}" --root "${ROOT}"
	)
	_run_unit_pytest "${SHARD_FILES[@]}"
	;;
gherkin-unit)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/unit/gherkin \
		--ignore=src/tests/integration \
		-m "gherkin and not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
quality_metrics)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/quality_metrics \
		--ignore=src/tests/integration \
		-m "not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
coverage)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/coverage \
		--ignore=src/tests/integration \
		-m "not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
mutation)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/mutation \
		--ignore=src/tests/integration \
		-m "not runtime" \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
qa)
	exec env -u CYT_RUN_INTEGRATION_TESTS -u CYT_RUN_RUNTIME_TESTS uv run pytest \
		src/tests/qa \
		-m qa \
		--run-qa \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
runtime)
	exec env CYT_RUN_RUNTIME_TESTS=1 uv run pytest \
		src/tests/runtime \
		-m runtime \
		--run-runtime \
		"${PYTEST_VERBOSE_ARGS[@]}" \
		"${PYTEST_XDIST_ARGS[@]}" \
		"${@:2}"
	;;
*)
	echo "unknown category: ${category} (expected unit|unit-shard|gherkin-unit|quality_metrics|coverage|mutation|qa|runtime)" >&2
	exit 1
	;;
esac
