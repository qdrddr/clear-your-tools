#!/usr/bin/env bash
# Update sdk/rust/cyt-indexer/Cargo.toml, sdk/python/pyproject.toml, and sdk/typescript/package.json first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CARGO_LOCK="${ROOT}/Cargo.lock"
PYPROJECT_TOML="${ROOT}/sdk/python/pyproject.toml"
PACKAGE_JSON="${ROOT}/sdk/typescript/package.json"
PACKAGE_LOCK="${ROOT}/sdk/typescript/package-lock.json"
UV_LOCK="${ROOT}/uv.lock"
PUBLISH="${ROOT}/search/publish.sh"
TAG_FILE="${ROOT}/search/.publish-tag"

read_version() {
	grep -E '^version[[:space:]]*=' "${CARGO_TOML}" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_cargo_lock_version() {
	awk '
    /^name = "cyt-indexer"$/ { found=1; next }
    found && /^version = / {
      gsub(/^version = "|"$/, "", $0)
      print $0
      exit
    }
  ' "${CARGO_LOCK}"
}

update_cargo_lock_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    /^name = "cyt-indexer"$/ { found=1 }
    found && /^version = / {
      print "version = \"" version "\""
      found=0
      next
    }
    { print }
  ' "${CARGO_LOCK}" >"${tmp}"
	mv "${tmp}" "${CARGO_LOCK}"
}

read_pyproject_version() {
	grep -E '^version[[:space:]]*=' "${PYPROJECT_TOML}" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_package_json_version() {
	grep -E '"version"[[:space:]]*:' "${PACKAGE_JSON}" |
		head -1 |
		sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"(.*)".*/\1/'
}

read_package_lock_version() {
	grep -E '"version"[[:space:]]*:' "${PACKAGE_LOCK}" |
		head -1 |
		sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"(.*)".*/\1/'
}

update_package_json_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	sed -E "0,/^  \"version\": \".*\"/s//  \"version\": \"${version}\"/" "${PACKAGE_JSON}" >"${tmp}"
	mv "${tmp}" "${PACKAGE_JSON}"
}

update_package_lock_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    BEGIN { root_done=0; pkg_done=0 }
    !root_done && /^  "version": "/ {
      print "  \"version\": \"" version "\","
      root_done=1
      next
    }
    !pkg_done && /^      "version": "/ {
      print "      \"version\": \"" version "\","
      pkg_done=1
      next
    }
    { print }
  ' "${PACKAGE_LOCK}" >"${tmp}"
	mv "${tmp}" "${PACKAGE_LOCK}"
}

read_uv_lock_version() {
	awk '
    /^name = "cyt-indexer-sdk"$/ { found=1; next }
    found && /^version = / {
      gsub(/^version = "|"$/, "", $0)
      print $0
      exit
    }
  ' "${UV_LOCK}"
}

update_uv_lock_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    /^name = "cyt-indexer-sdk"$/ { found=1 }
    found && /^version = / {
      print "version = \"" version "\""
      found=0
      next
    }
    { print }
  ' "${UV_LOCK}" >"${tmp}"
	mv "${tmp}" "${UV_LOCK}"
}

version="$(read_version)"
pyproject_version="$(read_pyproject_version)"
package_json_version="$(read_package_json_version)"

if [[ -z "${version}" ]]; then
	echo "error: could not read version from ${CARGO_TOML}" >&2
	exit 1
fi

if [[ -z "${pyproject_version}" ]]; then
	echo "error: could not read version from ${PYPROJECT_TOML}" >&2
	exit 1
fi

if [[ -z "${package_json_version}" ]]; then
	echo "error: could not read version from ${PACKAGE_JSON}" >&2
	exit 1
fi

if [[ ! -f "${CARGO_LOCK}" ]]; then
	echo "error: missing ${CARGO_LOCK}" >&2
	exit 1
fi

if [[ ! -f "${UV_LOCK}" ]]; then
	echo "error: missing ${UV_LOCK}" >&2
	exit 1
fi

if [[ ! -f "${PACKAGE_LOCK}" ]]; then
	echo "error: missing ${PACKAGE_LOCK}" >&2
	exit 1
fi

tag="v${version}"
lock_version="$(read_cargo_lock_version)"
uv_lock_version="$(read_uv_lock_version)"
npm_lock_version="$(read_package_lock_version)"

if [[ -z "${lock_version}" ]]; then
	echo "error: could not read cyt-indexer version from ${CARGO_LOCK}" >&2
	exit 1
fi

if [[ -z "${uv_lock_version}" ]]; then
	echo "error: could not read cyt-indexer-sdk version from ${UV_LOCK}" >&2
	exit 1
fi

if [[ -z "${npm_lock_version}" ]]; then
	echo "error: could not read cyt-indexer-sdk version from ${PACKAGE_LOCK}" >&2
	exit 1
fi

tag_synced=false
lock_synced=false
uv_lock_synced=false
npm_json_synced=false
npm_lock_synced=false

if [[ -f "${TAG_FILE}" ]] && grep -qxF "tag=${tag}" "${TAG_FILE}"; then
	tag_synced=true
fi

if [[ "${lock_version}" == "${version}" ]]; then
	lock_synced=true
fi

if [[ "${uv_lock_version}" == "${pyproject_version}" ]]; then
	uv_lock_synced=true
fi

if [[ "${package_json_version}" == "${pyproject_version}" ]]; then
	npm_json_synced=true
fi

if [[ "${npm_lock_version}" == "${pyproject_version}" ]]; then
	npm_lock_synced=true
fi

if [[ "${tag_synced}" == true && "${lock_synced}" == true && "${uv_lock_synced}" == true && "${npm_json_synced}" == true && "${npm_lock_synced}" == true ]]; then
	echo "${PUBLISH} tag=${tag}, Cargo.lock cyt-indexer=${version}, uv.lock cyt-indexer-sdk=${pyproject_version}, and npm cyt-indexer-sdk=${package_json_version} already synced"
	exit 0
fi

if [[ "${tag_synced}" != true ]]; then
	printf 'tag=%s\n' "${tag}" >"${TAG_FILE}"
	echo "set ${PUBLISH} tag=${tag} (from Cargo.toml version ${version})"
fi

if [[ "${lock_synced}" != true ]]; then
	update_cargo_lock_version "${version}"
	echo "set Cargo.lock cyt-indexer version=${version} (from Cargo.toml version ${version})"
fi

if [[ "${uv_lock_synced}" != true ]]; then
	update_uv_lock_version "${pyproject_version}"
	echo "set uv.lock cyt-indexer-sdk version=${pyproject_version} (from pyproject.toml version ${pyproject_version})"
fi

if [[ "${npm_json_synced}" != true ]]; then
	update_package_json_version "${pyproject_version}"
	echo "set ${PACKAGE_JSON} version=${pyproject_version} (from pyproject.toml version ${pyproject_version})"
fi

if [[ "${npm_lock_synced}" != true ]]; then
	update_package_lock_version "${pyproject_version}"
	echo "set ${PACKAGE_LOCK} cyt-indexer-sdk version=${pyproject_version} (from pyproject.toml version ${pyproject_version})"
fi

exit 0
