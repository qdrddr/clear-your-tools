#!/usr/bin/env bash
# Run cyt-indexer tests for one category (separate prek hooks per type).
#
# Usage:
#   ./scripts/local/tests/cargo-test-category.sh unit|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

category="${1:?usage: $0 unit|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa}"
case "${category}" in
unit | integration | cucumber | ffi | coverage | mutation | quality_metrics | qa) ;;
*)
	echo "unknown category: ${category} (expected unit|integration|cucumber|ffi|coverage|mutation|quality_metrics|qa)" >&2
	exit 1
	;;
esac

prefix="${category}_"

mapfile -t tests < <(
	grep -E "^name = \"${prefix}" "${CARGO_TOML}" | sed -E 's/^name = "(.+)"$/\1/'
)

if ((${#tests[@]} == 0)); then
	echo "no ${category} test targets found in ${CARGO_TOML}" >&2
	exit 1
fi

args=()
for test_name in "${tests[@]}"; do
	args+=(--test "${test_name}")
done

feature_args=()
case "${category}" in
unit) feature_args=(--no-default-features --features "testing,ffi") ;;
integration) feature_args=(--no-default-features) ;;
cucumber | coverage | mutation | quality_metrics | qa) feature_args=(--no-default-features --features testing) ;;
ffi) feature_args=(--no-default-features --features ffi) ;;
esac

chunk_cargo_locked "${ROOT}" test -p cyt-indexer --locked "${feature_args[@]}" "${args[@]}"
