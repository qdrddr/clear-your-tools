#!/usr/bin/env bash
# Run every prek hook one at a time, staging fixes after each, until all pass.
#
# Loops forever: on each pass every hook from .pre-commit-config.yaml is run
# individually with --all-files. After each hook, `git add *` stages any fixes
# so later hooks see updated files. The loop exits only when every hook succeeds
# in a single pass, or when you interrupt the script (Ctrl+C).
#
# Usage (from repo root):
#   ./search/prek-loop.sh
#
# Requires: git, uv, prek (via uv run)
#
# TASK:
# Run `prek run -a` iteratively, fix all issues, do not omit, comment out or ignore, instead investigate the root cause and fix. Preserve the functionality.

set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT" || exit 1

cleanup() {
	echo
	echo "Interrupted. Exiting prek loop."
	exit 130
}
trap cleanup INT TERM

mapfile -t HOOKS < <(uv run prek list | sed 's/^\.://' | awk '!seen[$0]++')

if ((${#HOOKS[@]} == 0)); then
	echo "No prek hooks found." >&2
	exit 1
fi

echo "Prek loop: ${#HOOKS[@]} hooks, running until all pass in one iteration."
echo "Hooks: ${HOOKS[*]}"
echo

iteration=0
while true; do
	iteration=$((iteration + 1))
	echo "========== iteration ${iteration} =========="
	all_passed=true
	failed_hooks=()

	for hook in "${HOOKS[@]}"; do
		echo
		echo ">>> ${hook}"
		if uv run prek run "$hook" --all-files; then
			echo "<<< ${hook}: ok"
		else
			echo "<<< ${hook}: failed"
			all_passed=false
			failed_hooks+=("$hook")
		fi
		# shellcheck disable=SC2035
		git add *
	done

	echo
	if $all_passed; then
		echo "All ${#HOOKS[@]} hooks passed on iteration ${iteration}."
		exit 0
	fi

	echo "Iteration ${iteration} finished with failures: ${failed_hooks[*]}"
	echo "Re-running from the top..."
	echo
done
