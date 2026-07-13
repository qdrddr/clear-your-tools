#!/usr/bin/env bash
# Run PSScriptAnalyzer via an existing Microsoft Container Registry PowerShell image.
# No custom image build; modules are cached in a named Docker volume.
set -euo pipefail

if (("$#" == 0)); then
	echo "usage: $0 -Check|-Format [psscriptanalyzer args...] FILE.ps1..." >&2
	exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
	echo "error: docker not found (required for psscriptanalyzer-*-docker hooks)" >&2
	exit 1
fi

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PS1="${SCRIPT_DIR}/psscriptanalyzer-docker.ps1"

IMAGE="${PSSA_DOCKER_IMAGE:-}"
if [[ -z "$IMAGE" ]]; then
	case "$(uname -m)" in
	arm64 | aarch64) IMAGE=mcr.microsoft.com/powershell:7.5-mariner-2.0-arm64 ;;
	*) IMAGE=mcr.microsoft.com/powershell:7.5-debian-12 ;;
	esac
fi

MODULE_VOLUME="${PSSA_DOCKER_MODULE_VOLUME:-clear-your-tools-pssa-modules}"

docker run --rm \
	--pull=missing \
	-v "${ROOT}:${ROOT}" \
	-v "${MODULE_VOLUME}:/root/.local/share/powershell/Modules" \
	-w "${ROOT}" \
	"${IMAGE}" \
	pwsh -NoProfile -File "${PS1}" "$@"
