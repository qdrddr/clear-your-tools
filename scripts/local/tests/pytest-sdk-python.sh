#!/usr/bin/env bash
# Run cyt-indexer-sdk Python binding tests (sdk/python/tests/unit).
#
# Usage:
#   ./scripts/local/tests/pytest-sdk-python.sh [pytest args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SDK="${ROOT}/sdk/python"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

ensure_native_import() {
	if env -u VIRTUAL_ENV uv run --directory "${SDK}" --no-sync python -c "import cyt_indexer._native" 2>/dev/null; then
		return 0
	fi
	if [[ "${SKIP_MATURIN_DEVELOP:-}" == 1 ]]; then
		echo "error: cyt_indexer._native is not importable (set SKIP_MATURIN_DEVELOP=0 to rebuild)" >&2
		return 1
	fi
	chunk_run_maturin_develop "${ROOT}" "${SDK}"
}

ensure_native_import
cd "${SDK}"
exec env -u CARGO_TARGET_DIR uv run --no-sync --with pytest pytest tests/unit "$@"
