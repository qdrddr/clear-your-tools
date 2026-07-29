#!/usr/bin/env bash
# Run cyt-indexer tests for one category (separate prek hooks per type).
#
# Usage:
#   ./scripts/cargo-test-category.sh unit|integration|cucumber|ffi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"

category="${1:?usage: $0 unit|integration|cucumber|ffi}"
case "${category}" in
unit | integration | cucumber | ffi) ;;
*)
	echo "unknown category: ${category} (expected unit|integration|cucumber|ffi)" >&2
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
unit) feature_args=(--features testing,ffi) ;;
integration) feature_args=() ;;
cucumber) feature_args=(--features testing) ;;
ffi) feature_args=(--no-default-features --features ffi) ;;
esac

cd "${ROOT}"
exec env -u CARGO_TARGET_DIR cargo test -p cyt-indexer "${feature_args[@]}" "${args[@]}"
