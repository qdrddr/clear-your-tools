#!/usr/bin/env bash
# Pre-compile cyt-indexer test binaries before parallel rust test lanes.
#
# Usage:
#   ./scripts/local/tests/cargo-warm-build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

mapfile -t unit_tests < <(
	grep -E '^name = "unit_' "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
)
mapfile -t integration_tests < <(
	grep -E '^name = "integration_' "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
)
mapfile -t testing_tests < <(
	grep -E '^name = "(cucumber|coverage|mutation|quality_metrics|qa)_' "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
)
mapfile -t ffi_tests < <(
	grep -E '^name = "ffi_' "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
)

_warm_profile() {
	local profile="$1"
	shift
	local -a tests=("$@")
	local -a feature_args=()
	local -a args=()
	local test_name

	if ((${#tests[@]} == 0)); then
		return 0
	fi

	case "${profile}" in
	unit) feature_args=(--no-default-features --features "testing,ffi") ;;
	integration) feature_args=(--no-default-features) ;;
	testing) feature_args=(--no-default-features --features testing) ;;
	ffi) feature_args=(--no-default-features --features ffi) ;;
	*)
		echo "unknown warm profile: ${profile}" >&2
		return 1
		;;
	esac

	for test_name in "${tests[@]}"; do
		args+=(--test "${test_name}")
	done

	chunk_cargo_locked "${ROOT}" test -p cyt-indexer --locked --no-run "${feature_args[@]}" "${args[@]}"
}

if ((${#unit_tests[@]})); then
	_warm_profile unit "${unit_tests[@]}"
fi
if ((${#integration_tests[@]})); then
	_warm_profile integration "${integration_tests[@]}"
fi
if ((${#testing_tests[@]})); then
	_warm_profile testing "${testing_tests[@]}"
fi
if ((${#ffi_tests[@]})); then
	_warm_profile ffi "${ffi_tests[@]}"
fi

if [[ "${CYT_DEFER_HEAL_CARGO_LOCK:-}" != 1 ]]; then
	bash "${ROOT}/scripts/local/dev/heal-cargo-lock.sh"
fi
