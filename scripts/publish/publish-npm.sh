#!/usr/bin/env bash
# Manual bootstrap or emergency publish of cyt-indexer-sdk to npm.
#
# Normal releases: push tag vX.Y.Z with publish-git.sh; CI runs
# .github/workflows/publish-npm-sdk.yml after publish-crates.yml succeeds.
#
# Usage:
#   ./scripts/publish/publish-npm.sh
#
# Prerequisites:
#   - Version already synced (./scripts/publish/sync-version.sh or publish-git.sh)
#   - npm login (one-time; OIDC cannot create a brand-new package)
#   - Every cyt-indexer-sdk.*.node for all six platforms in sdk/typescript/
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SDK_DIR="${ROOT}/sdk/typescript"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../lib/shorten-paths.sh"
export SHORTEN_ROOT="${ROOT}"

usage() {
	cat <<EOF
Usage: $(basename "$0")

Publish cyt-indexer-sdk to npm from ${SDK_DIR}.

Version is read from ${ROOT}/pyproject.toml (must match sdk/typescript/package.json).

For routine releases, use publish-git.sh instead; CI publishes all platforms via
.github/workflows/publish-npm-sdk.yml.
EOF
}

read_root_version() {
	grep -E '^version[[:space:]]*=' "${ROOT}/pyproject.toml" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_npm_version() {
	grep -E '^  "version": "' "${SDK_DIR}/package.json" |
		head -1 |
		sed -E 's/^  "version": "(.*)",/\1/'
}

require_command() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "error: required command not found: ${cmd}" >&2
		exit 1
	fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ $# -ne 0 ]]; then
	usage >&2
	exit 1
fi

require_command npm

version="$(read_root_version)"
npm_version="$(read_npm_version)"
if [[ -z "${version}" ]]; then
	echo "error: could not read version from pyproject.toml" >&2
	exit 1
fi
if [[ "${version}" != "${npm_version}" ]]; then
	printf 'error: version mismatch: pyproject.toml=%s package.json=%s\n' \
		"${version}" "${npm_version}" >&2
	printf 'run %s first\n' "${SCRIPT_DIR}/sync-version.sh" | shorten_paths >&2
	exit 1
fi

echo "publishing cyt-indexer-sdk@${version} to npm"
npm whoami

cd "${SDK_DIR}"
npm ci
npm run build:js

shopt -s nullglob
nodes=(cyt-indexer-sdk.*.node)
if ((${#nodes[@]} == 0)); then
	cat <<EOF | shorten_paths >&2
error: no cyt-indexer-sdk.*.node files in ${SDK_DIR}
manual publish needs every platform binary before npm publish.
build locally with npm run build:native (current platform only) or copy artifacts from CI.
EOF
	exit 1
fi

npm publish --access public

cat <<EOF | shorten_paths
published cyt-indexer-sdk@${version} to npm
  package: https://www.npmjs.com/package/cyt-indexer-sdk/v/${version}
EOF
