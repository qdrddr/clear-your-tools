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
PYTEST_VERBOSE_ARGS=()
if [[ "${CYT_PREK_VERBOSE_PYTEST:-}${CYT_PREK_PARALLEL_LOG:-}" == *1* ]]; then
	PYTEST_VERBOSE_ARGS=(-v --capture=tee-sys --durations=25 --durations-min=0.1)
fi
# SDK tests run in an isolated uv project without pytest-xdist; parallel sharding
# is handled by prek-loop-py-parallel.sh groups, not in-process -n auto here.
exec env -u CARGO_TARGET_DIR uv run --no-sync --with pytest pytest tests/unit "${PYTEST_VERBOSE_ARGS[@]}" "$@"
