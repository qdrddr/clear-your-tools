#!/usr/bin/env bash
# Run all registry E2E smokes locally (requires packages already on registries).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CYT_RELEASE_VERSION="${CYT_RELEASE_VERSION:?set CYT_RELEASE_VERSION}"

"${ROOT}/scripts/render-manifests.sh"

echo "=== Rust (crates.io) ==="
"${ROOT}/scripts/wait-registry.sh" crate
(cd "${ROOT}/rust" && cargo test)

echo "=== Python SDK (PyPI) ==="
"${ROOT}/scripts/wait-registry.sh" pypi-sdk
(cd "${ROOT}/python" && uv sync --group test && uv run pytest)

echo "=== TypeScript SDK (npm) ==="
"${ROOT}/scripts/wait-registry.sh" npm
(cd "${ROOT}/typescript" && npm install && node --test test/*.test.mjs)

echo "=== clear-your-tools (PyPI) ==="
"${ROOT}/scripts/wait-registry.sh" pypi-app
(cd "${ROOT}/clear-your-tools" && uv sync --group test && uv run pytest)

echo "All registry E2E smokes passed."
