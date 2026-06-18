#!/usr/bin/env bash
# Shorten absolute paths on stdin: repo root -> ./, home -> ~/
# Usage: ... | ./scripts/shorten-paths.sh
#    or: source ./scripts/shorten-paths.sh && ... | shorten_paths
set -uo pipefail

sed_escape() { printf '%s' "$1" | sed 's/[\/&]/\\&/g'; }

shorten_paths() {
	local root home_dir re_root re_home
	root="${SHORTEN_ROOT:-$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)}"
	home_dir="$(cd ~ && pwd -P)"
	re_root=$(sed_escape "${root%/}")
	re_home=$(sed_escape "$home_dir")

	sed \
		-e "s|${re_root}/|./|g" -e "s|${re_root}|./|g" \
		-e "s|${re_home}/|~/|g" -e "s|${re_home}|~|g"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	shorten_paths
fi
