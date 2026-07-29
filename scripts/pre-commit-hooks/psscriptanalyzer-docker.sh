#!/usr/bin/env bash
# Run PSScriptAnalyzer via Docker when available; fall back to local pwsh.
set -euo pipefail

if (("$#" == 0)); then
	exit 0
fi

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PS1="${SCRIPT_DIR}/psscriptanalyzer-docker.ps1"

run_local() {
	if ! command -v pwsh >/dev/null 2>&1; then
		echo "error: pwsh not found and Docker is unavailable" >&2
		echo "hint: install PowerShell (brew install --cask powershell) or start Docker Desktop" >&2
		exit 1
	fi
	exec pwsh -NoProfile -File "${PS1}" "$@"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
	IMAGE="${PSSA_DOCKER_IMAGE:-}"
	if [[ -z $IMAGE ]]; then
		case "$(uname -m)" in
		arm64 | aarch64) IMAGE=mcr.microsoft.com/powershell:7.5-mariner-2.0-arm64 ;;
		*) IMAGE=mcr.microsoft.com/powershell:7.5-debian-12 ;;
		esac
	fi

	MODULE_VOLUME="${PSSA_DOCKER_MODULE_VOLUME:-clear-your-tools-pssa-modules}"

	exec docker run --rm \
		--pull=missing \
		-v "${ROOT}:${ROOT}" \
		-v "${MODULE_VOLUME}:/root/.local/share/powershell/Modules" \
		-w "${ROOT}" \
		"${IMAGE}" \
		pwsh -NoProfile -File "${PS1}" "$@"
fi

run_local "$@"
