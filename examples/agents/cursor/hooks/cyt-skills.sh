#!/usr/bin/env bash
# Cursor hook wrapper: adapts Cursor stdin/stdout, then runs cyt skill injection.
set -euo pipefail

# Required for pruning pipeline: [rerank, llm] (Keychain service "cyt", or already exported)
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(/usr/bin/security find-generic-password -s "cyt" -a "OPENROUTER_API_KEY" -w 2>/dev/null || true)}"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="${DEEPINFRA_API_KEY:-$(/usr/bin/security find-generic-password -s "cyt" -a "DEEPINFRA_API_KEY" -w 2>/dev/null || true)}"
export DEEPINFRA_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CYT_REPO="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
BRIDGE="${SCRIPT_DIR}/cursor-hook-bridge.py"
CLI="${CYT_REPO}/src/cyt/proxy/cli.py"

if [[ -f "${CYT_REPO}/pyproject.toml" && -f "${CLI}" ]]; then
	VENV_PY="${CYT_REPO}/.venv/bin/python"
	if [[ -x "${VENV_PY}" && -f "${BRIDGE}" ]]; then
		exec "${VENV_PY}" "${BRIDGE}"
	fi
	for uv in "${UV:-}" /opt/homebrew/bin/uv "${HOME}/.local/bin/uv"; do
		if [[ -n "${uv}" && -x "${uv}" ]]; then
			exec "${uv}" run --directory "${CYT_REPO}" python "${BRIDGE}"
		fi
	done
fi

for cyt in /opt/homebrew/bin/cyt "${HOME}/.local/bin/cyt"; do
	if [[ -x "${cyt}" && -f "${BRIDGE}" ]]; then
		exec python3 "${BRIDGE}"
	fi
done

echo "cyt-skills.sh: no python/uv/cyt found for ${CYT_REPO}" >&2
exit 127
