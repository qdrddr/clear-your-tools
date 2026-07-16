#!/usr/bin/env bash
# Manual bootstrap or emergency publish of cyt-indexer-sdk to PyPI.
#
# Normal releases: push tag vX.Y.Z with publish-git.sh; CI runs
# .github/workflows/publish-pypi-sdk.yml after publish-crates.yml succeeds.
# The clear-your-tools app package is published separately by publish-pypi.yml.
#
# Usage:
#   ./scripts/publish-pypi.sh
#
# Prerequisites:
#   - Version already synced (./scripts/sync-version.sh or publish-git.sh)
#   - uv, maturin, Rust toolchain
#   - PyPI credentials (~/.pypirc or UV_PUBLISH_TOKEN)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SDK_DIR="${ROOT}/sdk/python"
DIST_DIR="${ROOT}/dist"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/shorten-paths.sh"
export SHORTEN_ROOT="${ROOT}"

usage() {
	cat <<EOF
Usage: $(basename "$0")

Build and publish cyt-indexer-sdk to PyPI from ${SDK_DIR}.

Version is read from ${SDK_DIR}/pyproject.toml (must match root pyproject.toml).

For routine releases, use publish-git.sh instead; CI builds all platform wheels via
.github/workflows/publish-pypi-sdk.yml.
EOF
}

read_sdk_version() {
	grep -E '^version[[:space:]]*=' "${SDK_DIR}/pyproject.toml" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_root_version() {
	grep -E '^version[[:space:]]*=' "${ROOT}/pyproject.toml" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
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

require_command uv

version="$(read_sdk_version)"
root_version="$(read_root_version)"
if [[ -z "${version}" ]]; then
	echo "error: could not read version from sdk/python/pyproject.toml" >&2
	exit 1
fi
if [[ "${version}" != "${root_version}" ]]; then
	printf 'error: version mismatch: sdk/python=%s pyproject.toml=%s\n' \
		"${version}" "${root_version}" >&2
	printf 'run %s first\n' "${SCRIPT_DIR}/sync-version.sh" | shorten_paths >&2
	exit 1
fi

echo "publishing cyt-indexer-sdk==${version} to PyPI"
mkdir -p "${DIST_DIR}"

cd "${SDK_DIR}"
# Wheel for the current platform only (CI builds all platforms with cibuildwheel).
uv run maturin build --release --out "${DIST_DIR}"
uv build --sdist --out-dir "${DIST_DIR}"

uv publish --publish-url https://upload.pypi.org/legacy/ "${DIST_DIR}"/*

cat <<EOF | shorten_paths
published cyt-indexer-sdk==${version} to PyPI
  package: https://pypi.org/project/cyt-indexer-sdk/${version}/
EOF
