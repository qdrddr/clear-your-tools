#!/usr/bin/env bash
# Run ShellCheck for pre-commit/prek hooks.
# Prefer the pinned Docker image (matches koalaman/shellcheck-precommit v0.11.0);
# fall back to a local shellcheck binary when Docker is unavailable.
set -euo pipefail

if (("$#" == 0)); then
	exit 0
fi

IMAGE="${SHELLCHECK_DOCKER_IMAGE:-docker.io/koalaman/shellcheck:v0.11.0}"
ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"

resolve_shellcheck_source() {
	local file="$1"
	local src="$2"
	local dir

	if [[ "${src}" == /* && -f "${src}" ]]; then
		printf '%s\n' "${src}"
		return 0
	fi

	dir="$(cd "$(dirname "${file}")" && pwd -P)"
	if [[ -f "${dir}/${src}" ]]; then
		printf '%s\n' "${dir}/${src}"
		return 0
	fi

	if [[ -f "${ROOT}/${src}" ]]; then
		printf '%s\n' "${ROOT}/${src}"
		return 0
	fi

	return 1
}

collect_shellcheck_files() {
	local -a inputs=("$@")
	local -A seen=()
	local -a resolved=()
	local file src candidate

	for file in "${inputs[@]}"; do
		[[ -f "${file}" ]] || continue
		candidate="$(cd "$(dirname "${file}")" && pwd -P)/$(basename "${file}")"
		if [[ -z "${seen[${candidate}]+x}" ]]; then
			seen["${candidate}"]=1
			resolved+=("${candidate}")
		fi

		mapfile -t SOURCE_PATHS < <(
			grep -E 'shellcheck[[:space:]]+source=' "${file}" 2>/dev/null |
				sed -E 's/.*shellcheck[[:space:]]+source=([^[:space:]]+).*/\1/' ||
				true
		)
		for src in "${SOURCE_PATHS[@]}"; do
			[[ -n "${src}" ]] || continue
			candidate="$(resolve_shellcheck_source "${file}" "${src}" || true)"
			[[ -n "${candidate}" && -z "${seen[${candidate}]+x}" ]] || continue
			seen["${candidate}"]=1
			resolved+=("${candidate}")
		done
	done

	printf '%s\n' "${resolved[@]}"
}

mapfile -t SHELLCHECK_FILES < <(collect_shellcheck_files "$@")
((${#SHELLCHECK_FILES[@]})) || exit 0

run_docker() {
	docker run --rm \
		-v "${ROOT}:${ROOT}" \
		-w "${ROOT}" \
		"${IMAGE}" \
		"$@"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
	run_docker "${SHELLCHECK_FILES[@]}"
	exit $?
fi

if ! command -v shellcheck >/dev/null 2>&1; then
	echo "error: shellcheck not found and Docker is unavailable" >&2
	echo "hint: install shellcheck (brew install shellcheck) or start Docker Desktop" >&2
	exit 1
fi

local_version="$(shellcheck --version | awk '/version:/ {print $2}')"
if [[ ${local_version} != 0.11.* ]]; then
	printf 'warning: using local shellcheck %s (hook expects 0.11.x)\n' \
		"${local_version}" >&2
fi

shellcheck "${SHELLCHECK_FILES[@]}"
