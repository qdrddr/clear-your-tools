#!/usr/bin/env bash
# Usage: ./scripts/prek-loop.sh [--short] [--one-run] [--no-git-add] [-g|--group GROUP...]
#
# Run prek hooks one at a time, staging fixes after each, until all pass.
# Groups are optional; see scripts/prek-hook-groups.yaml:
#   py, rust, go, c, ts, uni
#
# Examples:
#   ./scripts/prek-loop.sh -g py
#   ./scripts/prek-loop.sh --group py ts
#
# If prek-hook-groups.yaml is missing, all hooks run regardless of --group.
# Examples:
# Run iteratively, fix all issues, do not omit, comment out or ignore, instead investigate the root cause and fix. Preserve the functionality:

# ./scripts/prek-loop.sh --short --one-run --group py rust ts uni
# ./scripts/prek-loop.sh --short --one-run --group rust go c uni

set -uo pipefail

SHORT=false
ONE_RUN=false
NO_GIT_ADD=false
SELECTED_GROUPS=()
while (($#)); do
	case "$1" in
	--short)
		SHORT=true
		shift
		;;
	--one-run)
		ONE_RUN=true
		shift
		;;
	--no-git-add)
		NO_GIT_ADD=true
		shift
		;;
	-g | --group)
		shift
		if ((${#} == 0)) || [[ ${1:-} == -* ]]; then
			echo "--group requires at least one group name." >&2
			echo "Usage: $0 [--short] [--one-run] [--no-git-add] [-g|--group GROUP...]" >&2
			exit 1
		fi
		while (($#)) && [[ $1 != -* ]]; do
			SELECTED_GROUPS+=("$1")
			shift
		done
		;;
	-h | --help)
		echo "Usage: $0 [--short] [--one-run] [--no-git-add] [-g|--group GROUP...]" >&2
		echo "Groups: py rust go c ts uni (see scripts/prek-hook-groups.yaml)" >&2
		exit 0
		;;
	-*)
		echo "Unknown option: $1" >&2
		echo "Usage: $0 [--short] [--one-run] [--no-git-add] [-g|--group GROUP...]" >&2
		exit 1
		;;
	*)
		echo "Unexpected argument: $1" >&2
		echo "Usage: $0 [--short] [--one-run] [--no-git-add] [-g|--group GROUP...]" >&2
		exit 1
		;;
	esac
done

ROOT="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
cd "$ROOT" || exit 1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GROUPS_FILE="$SCRIPT_DIR/prek-hook-groups.yaml"
export SHORTEN_ROOT="$ROOT"

trap 'echo; echo "Interrupted."; exit 130' INT TERM

echo "Discovering prek hooks..." >&2
mapfile -t ALL_HOOKS < <(uv run prek list | sed 's/^\.://' | awk '!seen[$0]++')
((${#ALL_HOOKS[@]})) || {
	echo "No prek hooks found." >&2
	exit 1
}

declare -A ALL_HOOK_SET=()
for hook in "${ALL_HOOKS[@]}"; do
	ALL_HOOK_SET["$hook"]=1
done

load_group_hooks() {
	local group="$1"
	uv run python - "$group" "$GROUPS_FILE" <<'PY'
import sys

import yaml

group, path = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

groups = data.get("groups") or {}
if group not in groups:
    known = ", ".join(sorted(groups))
    print(f"ERROR:unknown-group:{group}:{known}")
    sys.exit(2)

hooks = groups[group] or []
for hook in hooks:
    if hook:
        print(hook)
PY
}

resolve_hooks() {
	if ((${#SELECTED_GROUPS[@]} == 0)); then
		HOOKS=("${ALL_HOOKS[@]}")
		return 0
	fi

	if [[ ! -f $GROUPS_FILE ]]; then
		echo "Warning: $GROUPS_FILE not found; running all hooks (ignoring groups: ${SELECTED_GROUPS[*]})." >&2
		HOOKS=("${ALL_HOOKS[@]}")
		return 0
	fi

	declare -A SEEN_HOOKS=()
	HOOKS=()
	local group group_hooks_raw group_exit missing=()

	for group in "${SELECTED_GROUPS[@]}"; do
		group_exit=0
		group_hooks_raw=$(load_group_hooks "$group") || group_exit=$?
		if ((group_exit == 2)); then
			if [[ $group_hooks_raw == ERROR:unknown-group:* ]]; then
				local known="${group_hooks_raw#ERROR:unknown-group:"${group}":}"
				echo "Unknown group '$group'. Valid groups: $known" >&2
				exit 1
			fi
		fi
		if ((group_exit != 0)); then
			echo "Failed to read hook groups from $GROUPS_FILE." >&2
			exit 1
		fi

		mapfile -t GROUP_HOOKS <<<"${group_hooks_raw:-}"

		for hook in "${GROUP_HOOKS[@]}"; do
			[[ -z $hook ]] && continue
			if [[ -z ${ALL_HOOK_SET[$hook]+x} ]]; then
				missing+=("$group:$hook")
				continue
			fi
			if [[ -z ${SEEN_HOOKS[$hook]+x} ]]; then
				HOOKS+=("$hook")
				SEEN_HOOKS[$hook]=1
			fi
		done
	done

	if ((${#missing[@]})); then
		echo "Warning: groups list hooks not in prek config: ${missing[*]}" >&2
	fi

	if ((${#HOOKS[@]} == 0)); then
		echo "Groups (${SELECTED_GROUPS[*]}) have no runnable hooks." >&2
	fi
}

resolve_hooks

total=${#HOOKS[@]}
mode="Prek loop"
$SHORT && mode+=" (short)"
$ONE_RUN && mode+=" (one run)"
if ((${#SELECTED_GROUPS[@]})); then
	mode+=" [${SELECTED_GROUPS[*]}]"
fi
if $ONE_RUN; then
	echo "$mode: $total hooks, single iteration."
else
	echo "$mode: $total hooks until all pass."
fi
if ((${#SELECTED_GROUPS[@]})) && [[ -f $GROUPS_FILE ]] && ((total > 0)); then
	echo "Hooks: ${HOOKS[*]}"
fi
echo

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

if ((total == 0)); then
	echo "Nothing to run."
	exit 0
fi

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
			if ! $NO_GIT_ADD; then
				rtk git add -A >/dev/null 2>&1 || true
			fi
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
		if ! $NO_GIT_ADD; then
			rtk git add -A >/dev/null 2>&1 || true
		fi
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
