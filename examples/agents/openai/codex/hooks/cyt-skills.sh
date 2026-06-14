#!/usr/bin/env bash
# Codex hook wrapper for `cyt hook --stdin`. Codex hooks use a minimal PATH (inherit = "core"),
# so do not rely on `uv` or `cyt` being on PATH — use the repo venv or absolute paths.
set -euo pipefail

# Required for pruning pipeline: [rerank, llm] (Keychain service "nono", or already exported)
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(/usr/bin/security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w 2>/dev/null || true)}"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="${DEEPINFRA_API_KEY:-$(/usr/bin/security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w 2>/dev/null || true)}"
export DEEPINFRA_API_KEY

CYT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
CLI="${CYT_REPO}/src/cyt/proxy/cli.py"
DEBUG_ARGS=()
if [[ "${CYT_SKILLS_DEBUG:-}" == 1 || "${CYT_SKILLS_DEBUG:-}" == true ]]; then
	DEBUG_ARGS=(--debug)
fi

if [[ -f "${CYT_REPO}/pyproject.toml" && -f "${CLI}" ]]; then
	VENV_PY="${CYT_REPO}/.venv/bin/python"
	if [[ -x "${VENV_PY}" ]]; then
		exec "${VENV_PY}" "${CLI}" hook --stdin "${DEBUG_ARGS[@]}"
	fi
	for uv in "${UV:-}" /opt/homebrew/bin/uv "${HOME}/.local/bin/uv"; do
		if [[ -n "${uv}" && -x "${uv}" ]]; then
			exec "${uv}" run --directory "${CYT_REPO}" src/cyt/proxy/cli.py hook --stdin "${DEBUG_ARGS[@]}"
		fi
	done
fi

for cyt in /opt/homebrew/bin/cyt "${HOME}/.local/bin/cyt"; do
	if [[ -x "${cyt}" ]]; then
		exec "${cyt}" hook --stdin "${DEBUG_ARGS[@]}"
	fi
done

echo "cyt-skills.sh: no python/uv/cyt found for ${CYT_REPO}" >&2
exit 127
