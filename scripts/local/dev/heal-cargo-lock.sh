#!/usr/bin/env bash
# Restore [patch.crates-io] and chunk-your-* registry pins after patched root cargo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_write_workspace_cargo_patches "${ROOT}"
chunk_ensure_workspace_cargo_lock "${ROOT}"
