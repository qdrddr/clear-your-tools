#!/usr/bin/env bash
# Put the repo virtualenv on PATH (Unix .venv/bin or Windows .venv/Scripts).
set -euo pipefail

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
VENV_DIR="${ROOT}/.venv"

if [[ -d "${VENV_DIR}/Scripts" ]]; then
	export PATH="${VENV_DIR}/Scripts:${PATH}"
elif [[ -d "${VENV_DIR}/bin" ]]; then
	export PATH="${VENV_DIR}/bin:${PATH}"
fi
