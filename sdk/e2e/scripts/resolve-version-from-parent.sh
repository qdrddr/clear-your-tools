#!/usr/bin/env bash
# Resolve CYT_RELEASE_VERSION for workflow_run chains or workflow_dispatch input.
# Writes github output "version" (may be empty for manual dispatch without input).
set -euo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:?}"
INPUT_VERSION="${INPUT_VERSION:-}"
PARENT_RUN_ID="${PARENT_RUN_ID:-}"

VERSION=""
if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then
	if [[ -n "$INPUT_VERSION" ]]; then
		VERSION="$INPUT_VERSION"
	fi
elif [[ "$EVENT_NAME" == "workflow_run" ]]; then
	if [[ -z "$PARENT_RUN_ID" ]]; then
		echo "::error::workflow_run missing parent run id" >&2
		exit 1
	fi
	gh run download "${PARENT_RUN_ID}" \
		--repo "${GITHUB_REPOSITORY}" \
		--name cyt-release-version \
		--dir /tmp/cyt-release-version
	VERSION="$(tr -d '\n' </tmp/cyt-release-version/cyt-release-version.txt)"
	if [[ -z "$VERSION" ]]; then
		echo "::error::cyt-release-version artifact was empty" >&2
		exit 1
	fi
fi

{
	echo "version=${VERSION}"
} >>"${GITHUB_OUTPUT:?}"

if [[ -n "$VERSION" ]]; then
	echo "CYT_RELEASE_VERSION=${VERSION}"
fi
