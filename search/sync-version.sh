#!/usr/bin/env bash
# Update sdk/rust/cyt-indexer/Cargo.toml and sdk/python/pyproject.toml first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CARGO_LOCK="${ROOT}/Cargo.lock"
PYPROJECT_TOML="${ROOT}/sdk/python/pyproject.toml"
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

if [[ -z "${version}" ]]; then
	echo "error: could not read version from ${CARGO_TOML}" >&2
	exit 1
fi

if [[ -z "${pyproject_version}" ]]; then
	echo "error: could not read version from ${PYPROJECT_TOML}" >&2
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

tag="v${version}"
lock_version="$(read_cargo_lock_version)"
uv_lock_version="$(read_uv_lock_version)"

if [[ -z "${lock_version}" ]]; then
	echo "error: could not read cyt-indexer version from ${CARGO_LOCK}" >&2
	exit 1
fi

if [[ -z "${uv_lock_version}" ]]; then
	echo "error: could not read cyt-indexer-sdk version from ${UV_LOCK}" >&2
	exit 1
fi

tag_synced=false
lock_synced=false
uv_lock_synced=false

if [[ -f "${TAG_FILE}" ]] && grep -qxF "tag=${tag}" "${TAG_FILE}"; then
	tag_synced=true
fi

if [[ "${lock_version}" == "${version}" ]]; then
	lock_synced=true
fi

if [[ "${uv_lock_version}" == "${pyproject_version}" ]]; then
	uv_lock_synced=true
fi

if [[ "${tag_synced}" == true && "${lock_synced}" == true && "${uv_lock_synced}" == true ]]; then
	echo "${PUBLISH} tag=${tag}, Cargo.lock cyt-indexer=${version}, and uv.lock cyt-indexer-sdk=${pyproject_version} already synced"
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

exit 0
