#!/usr/bin/env bash
# Usage: ./scripts/prek-loop.sh [--short] [--one-run]
# Run `prek run -a` iteratively, fix all issues, do not omit, comment out or ignore, instead investigate the root cause and fix. Preserve the functionality.

set -uo pipefail

SHORT=false
ONE_RUN=false
while (($#)); do
	case "$1" in
	--short)
		SHORT=true
		;;
	--one-run)
		ONE_RUN=true
		;;
	*)
		echo "Usage: $0 [--short] [--one-run]" >&2
		exit 1
		;;
	esac
	shift
done

ROOT="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
cd "$ROOT" || exit 1

trap 'echo; echo "Interrupted."; exit 130' INT TERM

mapfile -t HOOKS < <(uv run prek list | sed 's/^\.://' | awk '!seen[$0]++')
((${#HOOKS[@]})) || {
	echo "No prek hooks found." >&2
	exit 1
}

total=${#HOOKS[@]}
mode="Prek loop"
$SHORT && mode+=" (short)"
$ONE_RUN && mode+=" (one run)"
if $ONE_RUN; then
	echo "$mode: $total hooks, single iteration."
else
	echo "$mode: $total hooks until all pass."
fi
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SHORTEN_ROOT="$ROOT"

# prek prints "hook-name.....<status>"; extract <status> from dot-padded lines.
parse_prek_output() {
	local parsed
	parsed=$(printf '%s\n' "$1" | awk '
		/\.{3,}/ {
			s = $0
			sub(/^.*\.{3,}/, "", s)
			if (s ~ /^\([^)]*\)Skipped$/) {
				reason = s
				sub(/^\(/, "", reason)
				sub(/\)Skipped$/, "", reason)
				s = "Skipped (" reason ")"
			}
			if (n++) statuses = statuses ", "
			statuses = statuses s
			next
		}
		{ details = details $0 ORS }
		END {
			gsub(/\n$/, "", details)
			if (statuses != "") {
				n = split(statuses, parts, ", ")
				deduped = ""
				for (i = 1; i <= n; i++) {
					seen = 0
					for (j = 1; j < i; j++) {
						if (parts[j] == parts[i]) {
							seen = 1
							break
						}
					}
					if (!seen) {
						if (deduped != "") deduped = deduped ", "
						deduped = deduped parts[i]
					}
				}
				n = split(deduped, parts, ", ")
				has_failed = has_skipped = 0
				for (i = 1; i <= n; i++) {
					if (parts[i] ~ /^Failed/) has_failed = 1
					else if (parts[i] ~ /^Skipped/) has_skipped = 1
				}
				filtered = ""
				for (i = 1; i <= n; i++) {
					keep = 0
					if (has_failed) keep = (parts[i] ~ /^Failed/)
					else if (has_skipped) keep = (parts[i] ~ /^Skipped/)
					else keep = (parts[i] == "Passed")
					if (keep) {
						if (filtered != "") filtered = filtered ", "
						filtered = filtered parts[i]
					}
				}
				statuses = filtered
			}
			print statuses "\031" details
		}
	')
	PREK_STATUSES="${parsed%%$'\031'*}"
	PREK_DETAILS="${parsed#*$'\031'}"
}

run_hook() {
	local output exit_code=0
	output=$(rtk uv run prek run "$1" --all-files 2>&1) || exit_code=$?
	parse_prek_output "$output"
	return "$exit_code"
}

iteration=0
while true; do
	iteration=$((iteration + 1))
	passed=0 failed=0
	failed_hooks=()
	loop_header_printed=false

	if ! $SHORT; then
		echo "# LOOP $iteration"
	fi

	for hook in "${HOOKS[@]}"; do
		n=$((passed + failed + 1))
		hook_failed=false
		if run_hook "$hook"; then
			passed=$((passed + 1))
			result="Passed"
		else
			hook_failed=true
			failed=$((failed + 1))
			failed_hooks+=("$hook")
			result="Failed"
		fi

		if $SHORT && ! $hook_failed; then
			rtk git add -A >/dev/null 2>&1 || true
			continue
		fi

		if $SHORT && ! $loop_header_printed; then
			echo "# LOOP $iteration"
			loop_header_printed=true
		fi

		if [[ -n $PREK_STATUSES ]]; then
			echo "$PREK_STATUSES [$n/$total] $hook ($passed passed, $failed failed)"
		else
			echo "$result [$n/$total] $hook ($passed passed, $failed failed)"
		fi
		[[ -n $PREK_DETAILS ]] && printf '%s\n' "$PREK_DETAILS" | "$SCRIPT_DIR/shorten-paths.sh"
		rtk git add -A >/dev/null 2>&1 || true
	done

	echo
	echo "Loop $iteration: $passed passed, $failed failed."
	if ((failed == 0)); then
		echo "All $total hooks passed."
		exit 0
	fi
	echo "Failures: ${failed_hooks[*]}"
	if $ONE_RUN; then
		exit 1
	fi
	echo "Re-running..."
	echo
done
