#!/usr/bin/env bash
# Parse CYT_RELEASE_VERSION from TAG (release tag name or env TAG).
# Usage: eval "$(./parse-version.sh)"  or  source after exporting TAG=...
set -euo pipefail

TAG="${TAG:-${CYT_RELEASE_VERSION:-}}"
if [[ -z "$TAG" ]]; then
	echo "::error::TAG or CYT_RELEASE_VERSION must be set" >&2
	exit 1
fi

if [[ "$TAG" =~ v([0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?)$ ]]; then
	CYT_RELEASE_VERSION="${BASH_REMATCH[1]}"
elif [[ "$TAG" =~ ([0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?)$ ]]; then
	CYT_RELEASE_VERSION="${BASH_REMATCH[1]}"
else
	echo "::error::Could not parse semver from tag: ${TAG}" >&2
	exit 1
fi

export CYT_RELEASE_VERSION
echo "CYT_RELEASE_VERSION=${CYT_RELEASE_VERSION}"
