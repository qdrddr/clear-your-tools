#!/usr/bin/env bash
# Restore chunk-your-* registry pins in Cargo.lock when patched root cargo stripped them.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_ensure_workspace_cargo_lock "${ROOT}"
