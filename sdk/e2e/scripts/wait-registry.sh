#!/usr/bin/env bash
# Wait until a published package version is available on a registry.
# Usage: CYT_RELEASE_VERSION=0.1.10 ./wait-registry.sh <crate|pypi-sdk|pypi-app|npm>
set -euo pipefail

TARGET="${1:-}"
VERSION="${CYT_RELEASE_VERSION:-}"
if [[ -z "$TARGET" || -z "$VERSION" ]]; then
	echo "usage: CYT_RELEASE_VERSION=x.y.z $0 <crate|pypi-sdk|pypi-app|npm>" >&2
	exit 1
fi

MAX_ATTEMPTS="${WAIT_REGISTRY_MAX_ATTEMPTS:-60}"
SLEEP_SECS="${WAIT_REGISTRY_SLEEP_SECS:-30}"

pypi_has_version() {
	local package="$1"
	local ver="$2"
	PYPI_PACKAGE="$package" PYPI_VERSION="$ver" python3 -c "
import json
import os
import urllib.request

pkg = os.environ['PYPI_PACKAGE']
ver = os.environ['PYPI_VERSION']
url = f'https://pypi.org/pypi/{pkg}/json'
with urllib.request.urlopen(url, timeout=30) as resp:
    data = json.load(resp)
releases = data.get('releases', {})
if ver not in releases or not releases[ver]:
    raise SystemExit(1)
"
}

crate_has_version() {
	local ver="$1"
	local url="https://crates.io/api/v1/crates/cyt-indexer/${ver}"
	curl -fsSL -o /dev/null -w "%{http_code}" "$url" | grep -q '^200$'
}

npm_has_version() {
	local ver="$1"
	npm view "cyt-indexer-sdk@${ver}" version 2>/dev/null | grep -qxF "$ver"
}

wait_loop() {
	local label="$1"
	shift
	local attempt=1
	while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
		if "$@"; then
			echo "${label} ${VERSION} is available (attempt ${attempt})"
			return 0
		fi
		echo "Waiting for ${label} ${VERSION}... (${attempt}/${MAX_ATTEMPTS})"
		sleep "$SLEEP_SECS"
		attempt=$((attempt + 1))
	done
	echo "::error::Timed out waiting for ${label} ${VERSION}" >&2
	return 1
}

case "$TARGET" in
crate)
	wait_loop "crates.io/cyt-indexer" crate_has_version "$VERSION"
	;;
pypi-sdk)
	wait_loop "PyPI/cyt-indexer-sdk" pypi_has_version "cyt-indexer-sdk" "$VERSION"
	;;
pypi-app)
	wait_loop "PyPI/cyt-indexer-sdk" pypi_has_version "cyt-indexer-sdk" "$VERSION"
	wait_loop "PyPI/clear-your-tools" pypi_has_version "clear-your-tools" "$VERSION"
	;;
npm)
	wait_loop "npm/cyt-indexer-sdk" npm_has_version "$VERSION"
	;;
*)
	echo "unknown target: ${TARGET}" >&2
	exit 1
	;;
esac
