#!/usr/bin/env bash
# Sparse-clone clear-your-tools at tag v0.6.4 and render go.mod with a replace directive.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="${CYT_RELEASE_VERSION:-0.6.4}"
TAG="v${VERSION}"
REPO="${CYT_GIT_REPO:-https://github.com/qdrddr/clear-your-tools.git}"
STAGING="${CYT_GIT_STAGING:-${ROOT}/.staging/${VERSION}}"

if [[ ! -f "${STAGING}/sdk/go/go.mod" ]]; then
	echo "Fetching ${TAG} into ${STAGING}..." >&2
	rm -rf "$STAGING"
	git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse "$REPO" "$STAGING"
	(
		cd "$STAGING"
		git sparse-checkout set sdk/rust/cyt-indexer sdk/c sdk/go
	)
fi

sed "s|@CYT_GIT_STAGING@|${STAGING}|g" "${ROOT}/go.mod.in" >"${ROOT}/go.mod"

# When developing inside the monorepo, overlay the fixed cyt-native-ensure tool onto the tag checkout.
MONOREPO_ROOT="$(cd "${ROOT}/../.." && pwd)"
if [[ -f "${MONOREPO_ROOT}/sdk/go/go.mod" && -f "${MONOREPO_ROOT}/Cargo.toml" ]]; then
	rsync -a "${MONOREPO_ROOT}/sdk/go/cmd/cyt-native-ensure/" "${STAGING}/sdk/go/cmd/cyt-native-ensure/"
	echo "Overlaid monorepo cyt-native-ensure onto ${TAG} checkout" >&2
fi

echo "Prepared staging=${STAGING}" >&2
printf '%s\n' "$STAGING"
