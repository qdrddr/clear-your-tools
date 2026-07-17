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

run_docker() {
	docker run --rm \
		-v "${ROOT}:${ROOT}" \
		-w "${ROOT}" \
		"${IMAGE}" \
		"$@"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
	run_docker "$@"
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

shellcheck "$@"
