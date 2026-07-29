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
cd "${ROOT}"

if ! command -v cargo-llvm-cov >/dev/null 2>&1; then
	echo "cargo-llvm-cov not found; install with: cargo install cargo-llvm-cov" >&2
	exit 1
fi

cargo llvm-cov -p cyt-indexer --features testing,ffi --lcov --output-path target/cyt-indexer-lcov.info
