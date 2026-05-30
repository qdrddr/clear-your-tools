#!/usr/bin/env bash
# Update pyproject.toml first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="${ROOT}/pyproject.toml"
UV_LOCK="${ROOT}/uv.lock"
PUBLISH="${ROOT}/search/publish.sh"
TAG_FILE="${ROOT}/search/.publish-tag"

read_version() {
	grep -E '^version[[:space:]]*=' "${PYPROJECT}" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_uv_lock_version() {
	awk '
    /^name = "clear-your-tools"$/ { found=1; next }
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
    /^name = "clear-your-tools"$/ { found=1 }
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

if [[ -z "${version}" ]]; then
	echo "error: could not read version from ${PYPROJECT}" >&2
	exit 1
fi

if [[ ! -f "${UV_LOCK}" ]]; then
	echo "error: missing ${UV_LOCK}" >&2
	exit 1
fi

tag="v${version}"
lock_version="$(read_uv_lock_version)"

if [[ -z "${lock_version}" ]]; then
	echo "error: could not read clear-your-tools version from ${UV_LOCK}" >&2
	exit 1
fi

tag_synced=false
lock_synced=false

if [[ -f "${TAG_FILE}" ]] && grep -qxF "tag=${tag}" "${TAG_FILE}"; then
	tag_synced=true
fi

if [[ "${lock_version}" == "${version}" ]]; then
	lock_synced=true
fi

if [[ "${tag_synced}" == true && "${lock_synced}" == true ]]; then
	echo "${PUBLISH} tag=${tag} and uv.lock clear-your-tools=${version} already synced (pyproject version ${version})"
	exit 0
fi

if [[ "${tag_synced}" != true ]]; then
	printf 'tag=%s\n' "${tag}" >"${TAG_FILE}"
	echo "set ${PUBLISH} tag=${tag} (from pyproject version ${version})"
fi

if [[ "${lock_synced}" != true ]]; then
	update_uv_lock_version "${version}"
	echo "set uv.lock clear-your-tools version=${version} (from pyproject version ${version})"
fi

exit 0
