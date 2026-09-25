#!/usr/bin/env bash
# Run cyt-indexer clippy in focused scopes (faster than --all-targets --all-features).
#
# Usage:
#   ./scripts/local/dev/cargo-clippy-cyt-indexer.sh core|cli|all
#
#   core — lib + integration/unit test targets (testing,ffi); main pre-commit path
#   cli  — cyt-indexer binary (cli feature)
#   all  — core then cli (legacy full gate)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

mode="${1:?usage: $0 core|cli|all}"

mapfile -t CLIPPY_LINT_ARGS <<'EOF'
-D
warnings
-W
clippy::all
-W
clippy::pedantic
-W
clippy::nursery
-W
clippy::cargo
-W
clippy::perf
-W
clippy::unwrap_used
-W
clippy::expect_used
-W
clippy::panic
-W
clippy::todo
-W
clippy::unimplemented
-W
clippy::dbg_macro
-W
clippy::missing_const_for_fn
-W
clippy::missing_panics_doc
-W
clippy::missing_errors_doc
-W
clippy::missing_safety_doc
-W
clippy::wildcard_imports
-W
clippy::enum_glob_use
-W
clippy::module_name_repetitions
-W
clippy::pub_use
-W
clippy::bool_comparison
-W
clippy::match_bool
-W
clippy::match_same_arms
-W
clippy::match_wild_err_arm
-W
clippy::needless_borrow
-W
clippy::needless_lifetimes
-W
clippy::inefficient_to_string
-W
clippy::large_enum_variant
-W
clippy::large_stack_arrays
-W
clippy::map_clone
-W
unused
-W
dead_code
EOF

_run_core() {
	chunk_cargo_locked "${ROOT}" clippy -p cyt-indexer --lib --tests \
		--no-default-features --features "testing,ffi" --locked -- "${CLIPPY_LINT_ARGS[@]}"
}

_run_cli() {
	chunk_cargo_locked "${ROOT}" clippy -p cyt-indexer --bins \
		--no-default-features --features cli --locked -- "${CLIPPY_LINT_ARGS[@]}"
}

case "${mode}" in
core) _run_core ;;
cli) _run_cli ;;
all)
	_run_core
	_run_cli
	;;
*)
	echo "unknown mode: ${mode} (expected core|cli|all)" >&2
	exit 1
	;;
esac
