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

echo "Prek loop: ${#HOOKS[@]} hooks, running until all pass in one loop."
echo "Hooks: ${HOOKS[*]}"
echo

total_hooks=${#HOOKS[@]}
iteration=0
while true; do
	iteration=$((iteration + 1))
	processed=0
	passed=0
	failed=0
	all_passed=true
	failed_hooks=()

	echo "========== loop ${iteration} (0/${total_hooks} processed, 0 passed, 0 failed) =========="

	for hook in "${HOOKS[@]}"; do
		processed=$((processed + 1))
		echo
		echo ">>> loop ${iteration} [${processed}/${total_hooks}] ${hook} (${passed} passed, ${failed} failed so far)"
		if uv run prek run "$hook" --all-files; then
			passed=$((passed + 1))
			echo "<<< ${hook}: ok"
		else
			failed=$((failed + 1))
			all_passed=false
			failed_hooks+=("$hook")
			echo "<<< ${hook}: failed"
		fi
		# shellcheck disable=SC2035
		git add * >/dev/null 2>&1
	done

	echo
	echo "Loop ${iteration} complete: ${processed}/${total_hooks} processed, ${passed} passed, ${failed} failed."
	if $all_passed; then
		echo "All ${total_hooks} hooks passed on loop ${iteration}."
		exit 0
	fi

	echo "Failures: ${failed_hooks[*]}"
	echo "Re-running from the top..."
	echo
done
