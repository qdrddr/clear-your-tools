#!/usr/bin/env bash
# Update sdk/rust/cyt-indexer/Cargo.toml first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CARGO_LOCK="${ROOT}/Cargo.lock"
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

version="$(read_version)"

if [[ -z "${version}" ]]; then
	echo "error: could not read version from ${CARGO_TOML}" >&2
	exit 1
fi

if [[ ! -f "${CARGO_LOCK}" ]]; then
	echo "error: missing ${CARGO_LOCK}" >&2
	exit 1
fi

tag="v${version}"
lock_version="$(read_cargo_lock_version)"

if [[ -z "${lock_version}" ]]; then
	echo "error: could not read cyt-indexer version from ${CARGO_LOCK}" >&2
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
	echo "${PUBLISH} tag=${tag} and Cargo.lock cyt-indexer=${version} already synced (Cargo.toml version ${version})"
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

exit 0
