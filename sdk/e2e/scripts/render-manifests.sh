#!/usr/bin/env bash
# Render gitignored manifests from .in templates using CYT_RELEASE_VERSION.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${CYT_RELEASE_VERSION:-}"

if [[ -z "$VERSION" ]]; then
	if [[ -n "${TAG:-}" ]]; then
		# shellcheck source=parse-version.sh
		eval "$("${ROOT}/scripts/parse-version.sh")"
	else
		echo "CYT_RELEASE_VERSION or TAG must be set" >&2
		exit 1
	fi
fi

render() {
	local src="$1"
	local dst="$2"
	sed "s/@CYT_RELEASE_VERSION@/${VERSION}/g" "$src" >"$dst"
	echo "rendered ${dst}"
}

render "${ROOT}/rust/Cargo.toml.in" "${ROOT}/rust/Cargo.toml"
render "${ROOT}/python/pyproject.toml.in" "${ROOT}/python/pyproject.toml"
render "${ROOT}/typescript/package.json.in" "${ROOT}/typescript/package.json"
render "${ROOT}/clear-your-tools/pyproject.toml.in" "${ROOT}/clear-your-tools/pyproject.toml"
