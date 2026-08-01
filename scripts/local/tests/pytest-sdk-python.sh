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

if [[ "${SKIP_MATURIN_DEVELOP:-}" != 1 ]]; then
	chunk_run_maturin_develop "${ROOT}" "${SDK}"
fi
cd "${SDK}"
exec env -u CARGO_TARGET_DIR uv run --no-sync --with pytest pytest tests/unit "$@"
