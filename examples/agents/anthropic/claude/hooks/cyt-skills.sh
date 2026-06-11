#!/usr/bin/env bash
# Claude Code hook wrapper for `cyt skills`.
set -euo pipefail

CYT_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../../.." && pwd)"
CLI="${CYT_REPO}/src/cyt/proxy/cli.py"
DEBUG_ARGS=()
if [[ "${CYT_SKILLS_DEBUG:-}" == 1 || "${CYT_SKILLS_DEBUG:-}" == true ]]; then
	DEBUG_ARGS=(--debug)
fi

if [[ -f "${CYT_REPO}/pyproject.toml" && -f "${CLI}" ]]; then
	VENV_PY="${CYT_REPO}/.venv/bin/python"
	if [[ -x "${VENV_PY}" ]]; then
		exec "${VENV_PY}" "${CLI}" skills "${DEBUG_ARGS[@]}"
	fi
	for uv in "${UV:-}" /opt/homebrew/bin/uv "${HOME}/.local/bin/uv"; do
		if [[ -n "${uv}" && -x "${uv}" ]]; then
			exec "${uv}" run --directory "${CYT_REPO}" src/cyt/proxy/cli.py skills "${DEBUG_ARGS[@]}"
		fi
	done
fi

for cyt in /opt/homebrew/bin/cyt "${HOME}/.local/bin/cyt"; do
	if [[ -x "${cyt}" ]]; then
		exec "${cyt}" skills "${DEBUG_ARGS[@]}"
	fi
done

echo "cyt-skills.sh: no python/uv/cyt found for ${CYT_REPO}" >&2
exit 127
