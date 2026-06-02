#!/usr/bin/env bash
# Run one registry E2E harness (render manifests first via run-all.sh or run-local.sh).
# Usage: CYT_RELEASE_VERSION=x.y.z ./run-target.sh <rust|python|typescript|clear-your-tools>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
	echo "usage: CYT_RELEASE_VERSION=x.y.z $0 <rust|python|typescript|clear-your-tools>" >&2
	exit 1
fi

maybe_wait() {
	if [[ "${SKIP_REGISTRY_WAIT:-}" == "1" ]]; then
		echo "Skipping registry wait for ${1} (SKIP_REGISTRY_WAIT=1)"
		return 0
	fi
	"${ROOT}/scripts/wait-registry.sh" "$2"
}

case "$TARGET" in
rust)
	echo "=== Rust (crates.io) ==="
	maybe_wait "crates.io/cyt-indexer" crate
	(cd "${ROOT}/rust" && cargo test)
	;;
python)
	echo "=== Python SDK (PyPI) ==="
	maybe_wait "PyPI/cyt-indexer-sdk" pypi-sdk
	(cd "${ROOT}/python" && uv sync --group test && uv run pytest)
	;;
typescript)
	echo "=== TypeScript SDK (npm) ==="
	maybe_wait "npm/cyt-indexer-sdk" npm
	(cd "${ROOT}/typescript" && npm install && npm test)
	;;
clear-your-tools)
	echo "=== clear-your-tools (PyPI) ==="
	maybe_wait "PyPI/clear-your-tools" pypi-app
	(cd "${ROOT}/clear-your-tools" && uv sync --group test && uv run pytest)
	;;
*)
	echo "unknown target: ${TARGET}" >&2
	echo "expected: rust, python, typescript, clear-your-tools" >&2
	exit 1
	;;
esac
