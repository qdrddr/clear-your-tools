#!/usr/bin/env bash
# Propagate a single semver to all package manifests and lockfiles.
#
# Usage:
#   ./scripts/sync-version.sh [VERSION]
#
# If VERSION is omitted, read it from the root pyproject.toml.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/shorten-paths.sh"
export SHORTEN_ROOT="${ROOT}"
ROOT_PYPROJECT="${ROOT}/pyproject.toml"
CARGO_TOML="${ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CARGO_LOCK="${ROOT}/Cargo.lock"
CHUNK_YOUR_TOOLS_DIR="${ROOT}/chunk-your-tools"
CHUNK_YOUR_SKILLS_DIR="${ROOT}/chunk-your-skills"
SDK_PYPROJECT="${ROOT}/sdk/python/pyproject.toml"
PACKAGE_JSON="${ROOT}/sdk/typescript/package.json"
PACKAGE_LOCK="${ROOT}/sdk/typescript/package-lock.json"
C_CMAKE="${ROOT}/sdk/c/CMakeLists.txt"
GO_VERSION="${ROOT}/sdk/go/moduleversion/version.go"
UV_LOCK="${ROOT}/uv.lock"
TAG_FILE="${ROOT}/search/.publish-tag"

usage() {
	cat <<EOF
Usage: $(basename "$0") [VERSION]

Propagate VERSION to all manifests and lockfiles:
  - pyproject.toml (clear-your-tools + cyt-indexer-sdk dependency)
  - sdk/rust/cyt-indexer/Cargo.toml
  - Cargo.lock (cyt-indexer)
  - sdk/python/pyproject.toml
  - uv.lock (clear-your-tools + cyt-indexer-sdk)
  - sdk/typescript/package.json
  - sdk/typescript/package-lock.json
  - sdk/c/CMakeLists.txt (project VERSION)
  - sdk/go/moduleversion/version.go (Version)

If VERSION is omitted, read it from ${ROOT_PYPROJECT}.

When ${CARGO_TOML} declares chunk-your-tools and/or chunk-your-skills
versions under [dependencies], checkout the matching vX.Y.Z tags in the
respective git submodules after fetching/pulling.
EOF
}

read_root_pyproject_version() {
	grep -E '^version[[:space:]]*=' "${ROOT_PYPROJECT}" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

validate_version() {
	local version="$1"
	if [[ ! "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
		echo "error: invalid semver: ${version}" >&2
		exit 1
	fi
}

read_cargo_dep_version() {
	local crate="$1"
	local line version=""

	if [[ ! -f "${CARGO_TOML}" ]]; then
		return 0
	fi

	line="$(
		grep -E "^[[:space:]]*${crate}[[:space:]]*=" "${CARGO_TOML}" |
			head -1 ||
			true
	)"
	if [[ -z "${line}" ]]; then
		return 0
	fi

	if [[ "${line}" =~ version[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
		version="${BASH_REMATCH[1]}"
	elif [[ "${line}" =~ =[[:space:]]*\"([^\"]+)\" ]]; then
		version="${BASH_REMATCH[1]}"
	fi

	if [[ -n "${version}" ]]; then
		printf '%s\n' "${version}"
	fi
}

sync_chunk_submodule() {
	local name="$1"
	local dir="$2"
	local version="$3"
	local tag="v${version}"

	if [[ -z "${version}" ]]; then
		return 0
	fi

	validate_version "${version}"

	if [[ ! -e "${dir}/.git" ]]; then
		printf 'warning: submodule %s not initialized; skipping %s\n' \
			"${name}" "${tag}" | shorten_paths >&2
		return 0
	fi

	(
		cd "${dir}"
		if ! git fetch origin --tags 2>/dev/null; then
			printf 'warning: %s git fetch failed; using local tags only\n' \
				"${name}" | shorten_paths >&2
		fi
		git pull --ff-only 2>/dev/null || git pull --ff-only origin 2>/dev/null || true
		if ! git rev-parse --verify "${tag}^{commit}" >/dev/null 2>&1; then
			printf 'error: %s missing tag %s\n' "${name}" "${tag}" | shorten_paths >&2
			exit 1
		fi
		git checkout "${tag}"
	)

	printf 'synced submodule %s to %s\n' "${name}" "${tag}" | shorten_paths
}

sync_chunk_submodules_from_cargo() {
	local tools_version skills_version

	tools_version="$(read_cargo_dep_version "chunk-your-tools")"
	skills_version="$(read_cargo_dep_version "chunk-your-skills")"

	if [[ -z "${tools_version}" && -z "${skills_version}" ]]; then
		return 0
	fi

	if [[ -n "${tools_version}" ]]; then
		sync_chunk_submodule "chunk-your-tools" "${CHUNK_YOUR_TOOLS_DIR}" "${tools_version}"
	fi
	if [[ -n "${skills_version}" ]]; then
		sync_chunk_submodule "chunk-your-skills" "${CHUNK_YOUR_SKILLS_DIR}" "${skills_version}"
	fi
}

update_toml_version() {
	local file="$1"
	local version="$2"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    !done && /^version[[:space:]]*=/ {
      print "version = \"" version "\""
      done=1
      next
    }
    { print }
  ' "${file}" >"${tmp}"
	mv "${tmp}" "${file}"
}

update_root_pyproject_dependency() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	sed -E "s/\"cyt-indexer-sdk==[^\"]+\"/\"cyt-indexer-sdk==${version}\"/" \
		"${ROOT_PYPROJECT}" >"${tmp}"
	mv "${tmp}" "${ROOT_PYPROJECT}"
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

update_uv_lock_package_version() {
	local package_name="$1"
	local version="$2"
	local tmp
	tmp="$(mktemp)"
	awk -v package="${package_name}" -v version="${version}" '
    $0 == "name = \"" package "\"" { found=1 }
    found && /^version = / {
      print "version = \"" version "\""
      found=0
      next
    }
    { print }
  ' "${UV_LOCK}" >"${tmp}"
	mv "${tmp}" "${UV_LOCK}"
}

update_package_json_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    !done && /^  "version": "/ {
      print "  \"version\": \"" version "\","
      done=1
      next
    }
    { print }
  ' "${PACKAGE_JSON}" >"${tmp}"
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

update_cmake_project_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    /^project\(cyt-indexer-c VERSION / {
      print "project(cyt-indexer-c VERSION " version " LANGUAGES C)"
      next
    }
    { print }
  ' "${C_CMAKE}" >"${tmp}"
	mv "${tmp}" "${C_CMAKE}"
}

update_go_module_version() {
	local version="$1"
	local tmp
	tmp="$(mktemp)"
	awk -v version="${version}" '
    /^const Version = "/ {
      print "const Version = \"" version "\""
      next
    }
    { print }
  ' "${GO_VERSION}" >"${tmp}"
	mv "${tmp}" "${GO_VERSION}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ $# -gt 1 ]]; then
	usage >&2
	exit 1
fi

if [[ $# -eq 1 ]]; then
	version="$1"
else
	version="$(read_root_pyproject_version)"
	if [[ -z "${version}" ]]; then
		printf 'error: could not read version from %s\n' "${ROOT_PYPROJECT}" | shorten_paths >&2
		exit 1
	fi
fi

validate_version "${version}"

for file in \
	"${ROOT_PYPROJECT}" \
	"${CARGO_TOML}" \
	"${CARGO_LOCK}" \
	"${SDK_PYPROJECT}" \
	"${UV_LOCK}" \
	"${PACKAGE_JSON}" \
	"${PACKAGE_LOCK}" \
	"${C_CMAKE}" \
	"${GO_VERSION}"; do
	if [[ ! -f "${file}" ]]; then
		printf 'error: missing %s\n' "${file}" | shorten_paths >&2
		exit 1
	fi
done

tag="v${version}"

update_toml_version "${ROOT_PYPROJECT}" "${version}"
update_root_pyproject_dependency "${version}"
update_toml_version "${CARGO_TOML}" "${version}"
update_cargo_lock_version "${version}"
update_toml_version "${SDK_PYPROJECT}" "${version}"
update_uv_lock_package_version "clear-your-tools" "${version}"
update_uv_lock_package_version "cyt-indexer-sdk" "${version}"
update_package_json_version "${version}"
update_package_lock_version "${version}"
update_cmake_project_version "${version}"
update_go_module_version "${version}"
printf 'tag=%s\n' "${tag}" >"${TAG_FILE}"
sync_chunk_submodules_from_cargo

cat <<EOF | shorten_paths
synced version ${version} to:
  ${ROOT_PYPROJECT} (project + cyt-indexer-sdk dependency)
  ${CARGO_TOML}
  ${CARGO_LOCK} (cyt-indexer)
  ${SDK_PYPROJECT}
  ${UV_LOCK} (clear-your-tools + cyt-indexer-sdk)
  ${PACKAGE_JSON}
  ${PACKAGE_LOCK}
  ${C_CMAKE} (project VERSION)
  ${GO_VERSION} (Version)
  ${TAG_FILE} (tag=${tag})
EOF
