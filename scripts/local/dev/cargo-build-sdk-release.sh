#!/usr/bin/env bash
# Release build of cyt-indexer Python + Node SDK bindings and FOSSA cargo referenced-deps.
#
# FOSSA cargo@. resolves default features (cli) only. Building with python+node
# keeps the SDK binding graph current and regenerates fossa-deps.yml cargo entries.
#
# Usage:
#   ./scripts/local/dev/cargo-build-sdk-release.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

info() {
	printf 'info: %s\n' "$*"
}

info "resolve Cargo.lock for cyt-indexer python+node SDK features"
chunk_cargo_locked "${ROOT}" metadata \
	--manifest-path sdk/rust/cyt-indexer/Cargo.toml \
	--no-default-features --features python,node \
	--format-version 1 --quiet >/dev/null

info "cargo build -p cyt-indexer --no-default-features --features python,node --release --locked"
chunk_cargo_locked "${ROOT}" build -p cyt-indexer \
	--no-default-features --features python,node --release --locked

info "regenerate fossa-deps.yml (Python + Rust SDK referenced-dependencies)"
"${ROOT}/scripts/deps/export-requirements.sh"
