#!/usr/bin/env bash
# Run compiled node:test suites (glob expansion is shell-specific on Windows).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE="${1:?usage: run-node-tests.sh unit|parity}"
TEST_DIR="${ROOT}/dist/test/${SUITE}"

shopt -s nullglob
mapfile -t TEST_FILES < <(printf '%s\n' "${TEST_DIR}"/*.test.js)
((${#TEST_FILES[@]})) || {
	echo "error: no tests under ${TEST_DIR} (run npm run build:js first)" >&2
	exit 1
}

cd "${ROOT}"
exec node --test "${TEST_FILES[@]}"
