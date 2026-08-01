#!/usr/bin/env bash
# Run Rust coverage for cyt-indexer (requires cargo-llvm-cov).
#
# Install: cargo install cargo-llvm-cov
#
# Usage:
#   ./scripts/local/tests/rust-coverage.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

if ! command -v cargo-llvm-cov >/dev/null 2>&1; then
	echo "cargo-llvm-cov not found; install with: cargo install cargo-llvm-cov" >&2
	exit 1
fi

chunk_run_in_nopatch_workspace "${ROOT}" \
	env CARGO_TARGET_DIR="${ROOT}/target" \
	cargo llvm-cov -p cyt-indexer --features testing,ffi --lcov \
	--output-path "${ROOT}/target/cyt-indexer-lcov.info"
chunk_ensure_workspace_cargo_lock "${ROOT}"
