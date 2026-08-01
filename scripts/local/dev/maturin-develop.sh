#!/usr/bin/env bash
# maturin develop for sdk/python without rewriting root Cargo.toml patches long-term.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SDK_DIR="${ROOT}/sdk/python"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

run_maturin_develop() {
	# Root workflow hooks may export VIRTUAL_ENV=./.venv; force sdk/python project.
	exec env -u VIRTUAL_ENV -u CARGO_TARGET_DIR \
		CARGO_TARGET_DIR="${ROOT}/target" \
		uv run --directory "${SDK_DIR}" maturin develop --release "$@"
}

chunk_run_without_worktree_patches "${ROOT}" run_maturin_develop
