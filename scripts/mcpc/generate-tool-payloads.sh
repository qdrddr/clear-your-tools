#!/usr/bin/env bash
# Generate scripts/mcpc/payloads/<session>/<tool>.json from live mcpc sessions.
# Also writes scripts/mcpc/payloads/index-init/ and index-update/ variant payloads.
#
# Usage:
#   ./scripts/mcpc/generate-tool-payloads.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "$ROOT"
exec uv run python "${SCRIPT_DIR}/generate_tool_payloads.py" "$@"
