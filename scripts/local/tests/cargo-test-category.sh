#!/usr/bin/env bash
# Run cyt-indexer tests for one category (separate prek hooks per type).
#
# Usage:
#   ./scripts/local/tests/cargo-test-category.sh unit|unit-shard INDEX TOTAL|unit-one TARGET|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CYT_INDEXER_CRATE_DIR="${ROOT}/sdk/rust/cyt-indexer"
TIMINGS_JSONL="${ROOT}/target/.prek-parallel-logs/rust-unit-binary-timings.jsonl"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

category="${1:?usage: $0 unit|unit-shard INDEX TOTAL|unit-one TARGET|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa}"

RUST_TIMING_ENABLED=false
RUST_PARALLEL_TESTS=false
if [[ "${CYT_PREK_VERBOSE_RUST:-}${CYT_PREK_PARALLEL_LOG:-}" == *1* ]]; then
	RUST_TIMING_ENABLED=true
fi
if [[ "${CYT_RUST_PARALLEL_TESTS:-}" == 1 ]]; then
	RUST_PARALLEL_TESTS=true
fi

_maybe_heal_cargo_lock() {
	if [[ "${CYT_DEFER_HEAL_CARGO_LOCK:-}" == 1 ]]; then
		return 0
	fi
	bash "${ROOT}/scripts/local/dev/heal-cargo-lock.sh"
}

_find_test_executable() {
	local test_name="$1"
	local candidate="" path mtime=0 newest=0

	# Cargo test binaries are extensionless (e.g. unit_foo-deadbeef). Incremental
	# artifacts share the prefix but include dots (.rcgu.o, .d, …); never run those.
	while IFS= read -r path; do
		[[ -x ${path} ]] || continue
		mtime="$(stat -f '%m' "${path}" 2>/dev/null || stat -c '%Y' "${path}" 2>/dev/null || echo 0)"
		if ((mtime >= newest)); then
			newest=${mtime}
			candidate=${path}
		fi
	done < <(find "${ROOT}/target/debug/deps" -maxdepth 1 -type f -name "${test_name}-*" ! -name '*.*' 2>/dev/null)

	[[ -n ${candidate} ]] || return 1
	printf '%s\n' "${candidate}"
}

_cargo_feature_args() {
	local cat="$1"
	case "${cat}" in
	unit | unit-shard | unit-one) printf '%s\n' --no-default-features --features "testing,ffi" ;;
	integration) printf '%s\n' --no-default-features ;;
	cucumber | coverage | mutation | quality_metrics | qa) printf '%s\n' --no-default-features --features testing ;;
	ffi) printf '%s\n' --no-default-features --features ffi ;;
	*)
		echo "unknown category for features: ${cat}" >&2
		return 1
		;;
	esac
}

_list_category_tests() {
	local prefix="$1"
	grep -E "^name = \"${prefix}" "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
}

_record_timing() {
	local target="$1"
	local seconds="$2"
	local shard="${3:-}"
	local group="${4:-}"

	if ! $RUST_TIMING_ENABLED; then
		return 0
	fi

	printf '[timing] cargo-test %s %.2fs\n' "${target}" "${seconds}"
	mkdir -p "$(dirname "${TIMINGS_JSONL}")"
	printf '{"target":"%s","seconds":%.3f,"shard":"%s","group":"%s","ts":"%s"}\n' \
		"${target}" "${seconds}" "${shard}" "${group}" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >>"${TIMINGS_JSONL}"
}

_run_single_test_cargo() {
	local test_name="$1"
	local -a feature_args=()

	mapfile -t feature_args < <(_cargo_feature_args "${category}")
	chunk_cargo_locked "${ROOT}" test -p cyt-indexer --locked "${feature_args[@]}" --test "${test_name}"
}

_run_single_test() {
	local test_name="$1"
	local shard="${2:-}"
	local group="${3:-}"
	local exe=""
	local started finished elapsed

	started=$(date +%s.%N)
	if $RUST_PARALLEL_TESTS && exe="$(_find_test_executable "${test_name}")"; then
		# cargo test runs with cwd at the crate manifest; mirror that for direct binaries.
		(cd "${CYT_INDEXER_CRATE_DIR}" && "${exe}")
	else
		_run_single_test_cargo "${test_name}"
	fi
	finished=$(date +%s.%N)
	elapsed="$(awk -v s="${started}" -v f="${finished}" 'BEGIN { printf "%.2f", f - s }')"
	_record_timing "${test_name}" "${elapsed}" "${shard}" "${group}"
}

_run_tests_individually() {
	local shard="${1:-}"
	local group="${2:-}"
	local test_name

	for test_name in "${tests[@]}"; do
		_run_single_test "${test_name}" "${shard}" "${group}"
	done
}

_run_tests_batch() {
	local -a feature_args=()
	local -a args=()
	local test_name

	mapfile -t feature_args < <(_cargo_feature_args "${category}")
	for test_name in "${tests[@]}"; do
		args+=(--test "${test_name}")
	done
	chunk_cargo_locked "${ROOT}" test -p cyt-indexer --locked "${feature_args[@]}" "${args[@]}"
}

case "${category}" in
unit | integration | cucumber | ffi | coverage | mutation | quality_metrics | qa)
	prefix="${category}_"
	mapfile -t tests < <(_list_category_tests "${prefix}")
	if ((${#tests[@]} == 0)); then
		echo "no ${category} test targets found in ${CARGO_TOML}" >&2
		exit 1
	fi
	if $RUST_TIMING_ENABLED || $RUST_PARALLEL_TESTS; then
		_run_tests_individually
	else
		_run_tests_batch
	fi
	;;
unit-shard)
	shard_index="${2:?usage: $0 unit-shard INDEX TOTAL}"
	shard_total="${3:?usage: $0 unit-shard INDEX TOTAL}"
	mapfile -t tests < <(
		uv run python "${SCRIPT_DIR}/cargo-unit-shard.py" \
			--shard "${shard_index}" \
			--shards "${shard_total}" \
			--root "${ROOT}"
	)
	if ((${#tests[@]} == 0)); then
		echo "unit shard ${shard_index}/${shard_total} has no test targets" >&2
		exit 0
	fi
	category="unit-shard"
	export CYT_RUST_SHARD_INDEX="${shard_index}"
	export CYT_RUST_SHARD_TOTAL="${shard_total}"
	_run_tests_individually "${shard_index}" "rust-test-unit-shard-${shard_index}"
	;;
unit-one)
	test_name="${2:?usage: $0 unit-one TARGET}"
	tests=("${test_name}")
	category="unit-one"
	_run_single_test "${test_name}"
	;;
*)
	echo "unknown category: ${category} (expected unit|unit-shard|unit-one|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa)" >&2
	exit 1
	;;
esac

_maybe_heal_cargo_lock
