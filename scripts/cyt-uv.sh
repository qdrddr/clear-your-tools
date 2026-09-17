#!/usr/bin/env bash
# Dev checkout convenience wrapper — canonical script ships in src/cyt/hook/.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/../src/cyt/hook" && pwd)/uv.sh" "$@"
