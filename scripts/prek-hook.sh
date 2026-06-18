#!/usr/bin/env bash
# Run a local pre-commit hook command; print output with repo/home paths shortened.
set -uo pipefail

if (("$#" == 0)); then
	echo "Usage: $0 <command>" >&2
	exit 2
fi

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SHORTEN_ROOT="$ROOT"

bash -c "$*" 2>&1 | "$SCRIPT_DIR/shorten-paths.sh"
exit "${PIPESTATUS[0]}"
