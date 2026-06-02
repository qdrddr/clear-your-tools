#!/usr/bin/env bash
# Local entry point for registry E2E smokes (published packages only).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_NAME="$(basename "$0")"

usage() {
	cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS] [VERSION] [TARGET...]

Run registry E2E smoke tests against published packages (crates.io, PyPI, npm).

VERSION:
  Semver or release tag (e.g. 0.1.10 or v0.1.10). When omitted, uses the workspace
  version from sdk/rust/cyt-indexer/Cargo.toml.

TARGET:
  rust | python | typescript | clear-your-tools | all
  Default: all

OPTIONS:
  --skip-wait, -s   Skip polling registries (packages must already be published)
  --help, -h        Show this help

Environment:
  CYT_RELEASE_VERSION   Same as VERSION
  TAG                   Release tag to parse (e.g. v0.1.10)
  SKIP_REGISTRY_WAIT=1  Same as --skip-wait

Examples:
  ${SCRIPT_NAME}
  ${SCRIPT_NAME} 0.1.10
  ${SCRIPT_NAME} --skip-wait python
  ${SCRIPT_NAME} v0.1.10 rust typescript
EOF
}

workspace_version() {
	local cargo_toml="${ROOT}/../rust/cyt-indexer/Cargo.toml"
	if [[ ! -f "$cargo_toml" ]]; then
		echo "could not find workspace version (${cargo_toml})" >&2
		return 1
	fi
	awk -F'"' '/^version = / { print $2; exit }' "$cargo_toml"
}

resolve_version() {
	if [[ -n "${CYT_RELEASE_VERSION:-}" ]]; then
		return 0
	fi
	if [[ -n "${TAG:-}" ]]; then
		# shellcheck source=parse-version.sh
		eval "$("${ROOT}/scripts/parse-version.sh")"
		return 0
	fi
	if [[ -n "${VERSION_ARG:-}" ]]; then
		export TAG="${VERSION_ARG}"
		# shellcheck source=parse-version.sh
		eval "$("${ROOT}/scripts/parse-version.sh")"
		return 0
	fi
	local ws_version
	ws_version="$(workspace_version)"
	export CYT_RELEASE_VERSION="$ws_version"
	echo "Using workspace version CYT_RELEASE_VERSION=${CYT_RELEASE_VERSION}"
}

SKIP_WAIT="${SKIP_REGISTRY_WAIT:-0}"
TARGETS=()

while [[ $# -gt 0 ]]; do
	case "$1" in
	--skip-wait | -s)
		SKIP_WAIT=1
		shift
		;;
	--help | -h)
		usage
		exit 0
		;;
	all | rust | python | typescript | clear-your-tools)
		TARGETS+=("$1")
		shift
		;;
	-*)
		echo "unknown option: $1" >&2
		usage >&2
		exit 1
		;;
	*)
		if [[ -n "${VERSION_ARG:-}" ]]; then
			echo "unexpected argument: $1" >&2
			usage >&2
			exit 1
		fi
		VERSION_ARG="$1"
		shift
		;;
	esac
done

if [[ ${#TARGETS[@]} -eq 0 ]]; then
	TARGETS=(all)
fi

resolve_version
export SKIP_REGISTRY_WAIT="$SKIP_WAIT"

echo "Registry E2E: CYT_RELEASE_VERSION=${CYT_RELEASE_VERSION}"
"${ROOT}/scripts/render-manifests.sh"

run_all=0
for target in "${TARGETS[@]}"; do
	if [[ "$target" == "all" ]]; then
		run_all=1
		break
	fi
done

if [[ "$run_all" -eq 1 ]]; then
	for target in rust python typescript clear-your-tools; do
		"${ROOT}/scripts/run-target.sh" "$target"
	done
	echo "All registry E2E smokes passed."
else
	for target in "${TARGETS[@]}"; do
		"${ROOT}/scripts/run-target.sh" "$target"
	done
	echo "Registry E2E smoke passed for: ${TARGETS[*]}"
fi
