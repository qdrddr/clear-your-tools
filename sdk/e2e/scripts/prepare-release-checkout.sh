#!/usr/bin/env bash
# Prepare a tagged release checkout for Go/C E2E (not the active monorepo tree).
# Prints CYT_E2E_STAGING on stdout; status messages go to stderr.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${CYT_RELEASE_VERSION:?set CYT_RELEASE_VERSION}"
TAG="v${VERSION}"
REPO="${CYT_E2E_GIT_REPO:-https://github.com/qdrddr/clear-your-tools.git}"

default_staging() {
	local base="${TMPDIR:-/tmp}"
	printf '%s/cyt-e2e-%s' "${base%/}" "$VERSION"
}

if [[ "${CYT_E2E_USE_WORKSPACE:-}" == "1" ]]; then
	REPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
	STAGING="${CYT_E2E_STAGING:-$REPO_ROOT}"
	echo "Using workspace checkout CYT_E2E_STAGING=${STAGING}" >&2
	printf '%s\n' "$STAGING"
	exit 0
fi

STAGING="${CYT_E2E_STAGING:-$(default_staging)}"

if [[ -f "${STAGING}/sdk/go/go.mod" && -f "${STAGING}/Cargo.toml" ]]; then
	echo "Reusing release checkout CYT_E2E_STAGING=${STAGING}" >&2
	printf '%s\n' "$STAGING"
	exit 0
fi

echo "Fetching ${TAG} into ${STAGING}" >&2
rm -rf "$STAGING"
git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse "$REPO" "$STAGING"
(
	cd "$STAGING"
	git sparse-checkout set Cargo.toml Cargo.lock sdk/rust/cyt-indexer sdk/c sdk/go sdk/go/cmd/cyt-native-ensure
)

if [[ ! -f "${STAGING}/sdk/go/go.mod" ]]; then
	echo "::error::release checkout missing sdk/go/go.mod (${TAG})" >&2
	exit 1
fi

echo "Prepared CYT_E2E_STAGING=${STAGING}" >&2
printf '%s\n' "$STAGING"
