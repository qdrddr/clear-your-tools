#!/usr/bin/env bash
# Run cargo from the repo root with [patch.crates-io] worktree overrides.
# Restores Cargo.lock afterward so chunk-your-* keep crates.io source/checksum.
#
# Usage:
#   ./scripts/local/dev/cargo-patched.sh build -p cyt-indexer
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_run_patched_cargo "${ROOT}" "$@"
