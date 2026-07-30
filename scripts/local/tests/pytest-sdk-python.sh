#!/usr/bin/env bash
# Run cyt-indexer-sdk Python binding tests (sdk/python/tests/unit).
#
# Usage:
#   ./scripts/local/tests/pytest-sdk-python.sh [pytest args...]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SDK="${ROOT}/sdk/python"

if [[ "${SKIP_MATURIN_DEVELOP:-}" != 1 ]]; then
	env -u CARGO_TARGET_DIR uv run --directory "${SDK}" maturin develop --release
fi
exec env -u CARGO_TARGET_DIR uv run --directory "${SDK}" pytest tests/unit "$@"
