#!/usr/bin/env bash
# Bump version manifests, commit, push, tag, and create a GitHub Release.
#
# Usage:
#   ./scripts/publish/publish-git.sh v1.0.8
#   ./scripts/publish/publish-git.sh bump-patch
#   ./scripts/publish/publish-git.sh bump-minor
#   ./scripts/publish/publish-git.sh bump-major
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../lib/shorten-paths.sh"
export SHORTEN_ROOT="${ROOT}"

usage() {
	cat <<EOF
Usage: $(basename "$0") TAG | bump-patch | bump-minor | bump-major

Examples:
  $(basename "$0") v1.0.8
  $(basename "$0") bump-patch
  $(basename "$0") bump-minor
  $(basename "$0") bump-major

Auto-bump (bump-patch / bump-minor / bump-major):
  - Fetch the latest git tags and GitHub releases matching vMAJOR.MINOR.PATCH
  - Pick the highest version among both
  - bump-patch: increment PATCH, e.g. v1.0.7 -> v1.0.8
  - bump-minor: increment MINOR and reset PATCH to 0, e.g. v1.0.7 -> v1.1.0
  - bump-major: increment MAJOR and reset MINOR and PATCH to 0, e.g. v1.2.3 -> v2.0.0

Steps:
  1. Run scripts/publish/sync-version.sh with the semver (without the leading v)
  2. Commit only the version manifest files
  3. Push the current branch
  4. Force-create the git tag and push it
  5. Create (or recreate) a GitHub Release for the tag

Tag push triggers CI:
  publish-crates.yml -> npm SDK, PyPI SDK, C FFI, e2e -> publish-pypi.yml (clear-your-tools)
EOF
}

validate_tag() {
	local tag="$1"
	if [[ ! "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]]; then
		echo "error: invalid tag (expected vX.Y.Z): ${tag}" >&2
		exit 1
	fi
}

require_command() {
	local cmd="$1"
	if ! command -v "${cmd}" >/dev/null 2>&1; then
		echo "error: required command not found: ${cmd}" >&2
		exit 1
	fi
}

version_files() {
	cat <<EOF
${ROOT}/pyproject.toml
${ROOT}/Cargo.toml
${ROOT}/sdk/rust/cyt-indexer/Cargo.toml
${ROOT}/Cargo.lock
${ROOT}/sdk/python/pyproject.toml
${ROOT}/uv.lock
${ROOT}/sdk/typescript/package.json
${ROOT}/sdk/typescript/package-lock.json
${ROOT}/sdk/c/CMakeLists.txt
${ROOT}/sdk/go/moduleversion/version.go
${ROOT}/search/.publish-tag
EOF
}

stage_version_files() {
	local file

	mapfile -t files < <(version_files)
	for file in "${files[@]}"; do
		if [[ "${file}" == "${ROOT}/search/.publish-tag" ]]; then
			git add -f -- "${file}"
		else
			git add -- "${file}"
		fi
	done
}

semver_tag_pattern='^v[0-9]+\.[0-9]+\.[0-9]+$'

collect_version_tags() {
	git fetch origin --tags --quiet 2>/dev/null || true

	git tag -l 'v[0-9]*.[0-9]*.[0-9]*' |
		grep -E "${semver_tag_pattern}" || true

	gh release list --limit 1000 --json tagName --jq '.[].tagName' |
		grep -E "${semver_tag_pattern}" || true
}

latest_version_tag() {
	local -a versions=()

	mapfile -t versions < <(collect_version_tags | sort -uV)
	if ((${#versions[@]} == 0)); then
		echo "error: no vMAJOR.MINOR.PATCH tags or releases found; pass an explicit tag" >&2
		exit 1
	fi

	printf '%s\n' "${versions[-1]}"
}

resolve_bump_tag() {
	local bump_kind="$1"
	local latest major minor patch semver

	latest="$(latest_version_tag)"
	semver="${latest#v}"
	IFS='.' read -r major minor patch <<<"${semver}"

	case "${bump_kind}" in
	bump-patch)
		patch=$((patch + 1))
		;;
	bump-minor)
		minor=$((minor + 1))
		patch=0
		;;
	bump-major)
		major=$((major + 1))
		minor=0
		patch=0
		;;
	*)
		echo "error: unknown bump kind: ${bump_kind}" >&2
		exit 1
		;;
	esac

	printf 'v%s.%s.%s\n' "${major}" "${minor}" "${patch}"
}

previous_tag() {
	local tag="$1"
	git tag -l 'v[0-9]*.[0-9]*.[0-9]*' --sort=-version:refname |
		while read -r candidate; do
			if [[ "${candidate}" != "${tag}" ]]; then
				printf '%s\n' "${candidate}"
				return 0
			fi
		done
}

release_notes() {
	local tag="$1"
	local prev_tag repo

	prev_tag="$(previous_tag "${tag}")"
	repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"

	if [[ -n "${prev_tag}" ]]; then
		printf '**Full Changelog**: https://github.com/%s/compare/%s...%s\n' \
			"${repo}" "${prev_tag}" "${tag}"
	else
		printf 'Release %s\n' "${tag}"
	fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ $# -ne 1 ]]; then
	usage >&2
	exit 1
fi

require_command git
require_command gh

arg="$1"
case "${arg}" in
bump-patch | bump-minor | bump-major)
	tag="$(resolve_bump_tag "${arg}")"
	echo "${arg} resolved next tag: ${tag}"
	;;
*)
	tag="${arg}"
	;;
esac

validate_tag "${tag}"
semver="${tag#v}"

cd "${ROOT}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "error: not inside a git repository" >&2
	exit 1
fi

branch="$(git branch --show-current)"
if [[ -z "${branch}" ]]; then
	echo "error: detached HEAD; checkout a branch before publishing" >&2
	exit 1
fi

"${SCRIPT_DIR}/sync-version.sh" "${semver}"
"${ROOT}/scripts/local/dev/heal-cargo-lock.sh"

stage_version_files
if git diff --cached --quiet; then
	echo "version manifests already at ${semver}; skipping commit"
else
	# sync-version already refreshed manifests and Cargo.lock; skip hooks that
	# re-touch those files or rebuild native artifacts during the version commit.
	SKIP=sync-version,heal-cargo-lock,cargo-sort,verify-pins,go-test-repo-mod,pytest-sdk-python,local-dev-sdk-python,local-dev-sdk-go,local-dev-core-rust,local-dev-sdk-typescript,local-dev-sdk-c,local-dev-app,verify-sdk,build-c-lib-for-go \
		git commit -m "version bump to ${tag}"
fi

git push origin HEAD

git tag -f "${tag}"
git push -f origin "${tag}"

notes="$(release_notes "${tag}")"
if gh release view "${tag}" >/dev/null 2>&1; then
	gh release delete "${tag}" -y
fi
gh release create "${tag}" \
	--title "${tag}" \
	--notes "${notes}" \
	--prerelease

cat <<EOF | shorten_paths
published ${tag}:
  branch: ${branch}
  commit: $(git rev-parse --short HEAD)
  release: https://github.com/$(gh repo view --json nameWithOwner --jq .nameWithOwner)/releases/tag/${tag}
EOF
