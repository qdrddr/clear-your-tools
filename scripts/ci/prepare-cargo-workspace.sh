#!/usr/bin/env bash
# Strip workspace [patch.crates-io] overrides for CI and release builds.
#
# Local dev uses tag-pinned git worktrees (chunk-your-skills-vX.Y.Z/, etc.) via
# scripts/publish/sync-version.sh. Those directories are not in git; CI must
# resolve chunk-your-* from crates.io using Cargo.lock instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${ROOT}/scripts/lib/chunk-worktree.sh"

WORKSPACE="${ROOT}/Cargo.toml"
[[ -f "${WORKSPACE}" ]] || {
	echo "error: missing ${WORKSPACE}" >&2
	exit 1
}

TMP="$(mktemp)"
chunk_strip_workspace_patches "${WORKSPACE}" >"${TMP}"
if cmp -s "${WORKSPACE}" "${TMP}"; then
	rm -f "${TMP}"
	echo "Cargo.toml: no [patch.crates-io] overrides to strip."
else
	mv "${TMP}" "${WORKSPACE}"
	echo "Cargo.toml: stripped [patch.crates-io] (using Cargo.lock registry pins)."
fi
