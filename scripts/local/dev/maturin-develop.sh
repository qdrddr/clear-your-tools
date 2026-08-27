#!/usr/bin/env bash
# maturin develop for sdk/python without rewriting root Cargo.toml patches long-term.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SDK_DIR="${ROOT}/sdk/python"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

run_maturin_develop() {
	# Root workflow hooks may export VIRTUAL_ENV=./.venv; force sdk/python project.
	# Do not exec: chunk_run_without_worktree_patches must restore [patch.crates-io]
	# after maturin returns.
	local attempt=1 max_attempts=1 delay=3 pyd="${SDK_DIR}/src/cyt_indexer/_native.pyd"
	case "$(uname -s 2>/dev/null || echo unknown)" in
	MINGW* | MSYS* | CYGWIN*)
		max_attempts=6
		# pytest-unit loads _native.pyd; Windows may keep the DLL locked briefly.
		sleep 3
		;;
	esac

	while ((attempt <= max_attempts)); do
		if env -u VIRTUAL_ENV -u CARGO_TARGET_DIR \
			CARGO_TARGET_DIR="${ROOT}/target" \
			uv run --directory "${SDK_DIR}" maturin develop --release "$@"; then
			return 0
		fi
		if ((attempt >= max_attempts)); then
			return 1
		fi
		echo "maturin develop failed (attempt ${attempt}/${max_attempts}); retrying in ${delay}s..." >&2
		if [[ -f "${pyd}" ]]; then
			rm -f "${pyd}" 2>/dev/null || true
		fi
		sleep "${delay}"
		delay=$((delay * 2))
		attempt=$((attempt + 1))
	done
	return 1
}

chunk_run_without_worktree_patches "${ROOT}" run_maturin_develop
