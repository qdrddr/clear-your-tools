#!/usr/bin/env bash
# Run cargo-deny from the nopatch workspace so [patch.crates-io] does not
# rewrite Cargo.lock (root cargo deny adds registry lines to path-patch entries).
#
# Usage:
#   ./scripts/local/dev/cargo-deny.sh [cargo-deny args...]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_run_in_nopatch_workspace "${ROOT}" cargo deny --all-features check "$@"
