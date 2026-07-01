#!/usr/bin/env bash
# Run one registry E2E harness (render manifests first via run-all.sh or run-local.sh).
# Usage: CYT_RELEASE_VERSION=x.y.z ./run-target.sh <rust|python|typescript|clear-your-tools|go|c>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-}"

if [[ -z "$TARGET" ]]; then
	echo "usage: CYT_RELEASE_VERSION=x.y.z $0 <rust|python|typescript|clear-your-tools|go|c>" >&2
	exit 1
fi

maybe_wait() {
	if [[ "${SKIP_REGISTRY_WAIT:-}" == "1" ]]; then
		echo "Skipping registry wait for ${1} (SKIP_REGISTRY_WAIT=1)"
		return 0
	fi
	"${ROOT}/scripts/wait-registry.sh" "$2"
}

prepare_go_c() {
	_cyt_e2e_staging="$("${ROOT}/scripts/prepare-release-checkout.sh")"
	export CYT_E2E_STAGING="$_cyt_e2e_staging"
	unset _cyt_e2e_staging
	"${ROOT}/scripts/render-manifests.sh"
	"${ROOT}/scripts/build-staging-c-lib.sh"
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
	(cd "${ROOT}/python" && "${ROOT}/scripts/uv-sync-with-retry.sh" --group test && uv run pytest)
	;;
typescript)
	echo "=== TypeScript SDK (npm) ==="
	maybe_wait "npm/cyt-indexer-sdk" npm
	(cd "${ROOT}/typescript" && npm install && npm test)
	;;
clear-your-tools)
	echo "=== clear-your-tools (PyPI) ==="
	maybe_wait "PyPI/clear-your-tools" pypi-app
	(cd "${ROOT}/clear-your-tools" && "${ROOT}/scripts/uv-sync-with-retry.sh" --group test && uv run pytest)
	;;
go)
	echo "=== Go SDK (GitHub tag) ==="
	maybe_wait "GitHub tag v${CYT_RELEASE_VERSION}" tag
	prepare_go_c
	(cd "${ROOT}/go" && CGO_ENABLED=1 go mod tidy && CGO_ENABLED=1 go test ./...)
	;;
c)
	echo "=== C SDK (GitHub tag) ==="
	maybe_wait "GitHub tag v${CYT_RELEASE_VERSION}" tag
	prepare_go_c
	export CARGO_TARGET_DIR="${CYT_E2E_STAGING}/target"
	cmake -S "${ROOT}/c" -B "${ROOT}/c/build" -DCMAKE_BUILD_TYPE=Release \
		-DCYT_RUST_TARGET="${CYT_RUST_TARGET:-$("${ROOT}/scripts/host-rust-target.sh")}"
	cmake --build "${ROOT}/c/build"
	ctest --test-dir "${ROOT}/c/build" --output-on-failure
	;;
*)
	echo "unknown target: ${TARGET}" >&2
	echo "expected: rust, python, typescript, clear-your-tools, go, c" >&2
	exit 1
	;;
esac
