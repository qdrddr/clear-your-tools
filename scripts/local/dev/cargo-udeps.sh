#!/usr/bin/env bash
# Run cargo-udeps from the nopatch workspace (stable lockfile) with an isolated
# target dir. cargo-udeps requires nightly for -Z binary-dep-depinfo.
#
# Usage:
#   ./scripts/local/dev/cargo-udeps.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
UDEPS_TARGET="${CARGO_UDEPS_TARGET_DIR:-/tmp/fake-sandbox-target}"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

chunk_run_in_nopatch_workspace "${ROOT}" \
	env CARGO_TARGET_DIR="${UDEPS_TARGET}" cargo +nightly udeps "$@"
