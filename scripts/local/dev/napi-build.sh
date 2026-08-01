#!/usr/bin/env bash
# Build TypeScript native bindings without rewriting root Cargo.lock.
#
# napi's default --manifest-path resolves the workspace root Cargo.toml with
# [patch.crates-io], which strips chunk-your-* registry checksums from the lock.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

manifest="$(chunk_maturin_manifest_path "${ROOT}")"
napi_bin="${ROOT}/sdk/typescript/node_modules/.bin/napi"
if [[ ! -x "${napi_bin}" ]]; then
	echo "error: missing ${napi_bin} (run: cd sdk/typescript && npm ci)" >&2
	exit 1
fi

cd "${ROOT}/sdk/typescript"
exec env CARGO_TARGET_DIR="${ROOT}/target" \
	"${napi_bin}" build --platform --release \
	--manifest-path "${manifest}" \
	-p cyt-indexer --features node --no-default-features \
	--output-dir . --js native.cjs --dts native.d.ts "$@"
