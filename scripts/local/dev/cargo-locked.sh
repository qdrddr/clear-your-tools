#!/usr/bin/env bash
# Run cargo against Cargo.lock (crates.io), ignoring workspace [patch.crates-io] overrides.
#
# Usage:
#   ./scripts/local/dev/cargo-locked.sh build -p cyt-indexer --release --locked
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_cargo_locked "${ROOT}" "$@"
