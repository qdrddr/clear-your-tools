#!/usr/bin/env bash
# Export all tool definitions from live mcpc sessions into a single JSON file.
#
# Usage:
#   ./scripts/mcpc/export-mcp-definitions.sh [output.json]
#   ./scripts/mcpc/export-mcp-definitions.sh --session @fff [output.json]
#   ./scripts/mcpc/export-mcp-definitions.sh --skip annotations,execution [output.json]
#
# Default output: output/mcp-definitions.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${SCRIPT_DIR}/output/mcp-definitions.json"
SESSION=""
SKIP_KEYS=()

while [[ $# -gt 0 ]]; do
	case "$1" in
	--session)
		SESSION="${2:-}"
		if [[ -z "$SESSION" ]]; then
			echo "error: --session requires a session name (e.g. --session @fff)" >&2
			exit 1
		fi
		shift 2
		;;
	--skip)
		if [[ -z "${2:-}" ]]; then
			echo "error: --skip requires key name(s) (e.g. --skip annotations,execution)" >&2
			exit 1
		fi
		IFS=',' read -r -a _skip_batch <<<"$2"
		for key in "${_skip_batch[@]}"; do
			key="${key#"${key%%[![:space:]]*}"}"
			key="${key%"${key##*[![:space:]]}"}"
			if [[ -z "$key" ]]; then
				continue
			fi
			if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_-]*$ ]]; then
				echo "error: invalid --skip key: ${key}" >&2
				exit 1
			fi
			SKIP_KEYS+=("$key")
		done
		shift 2
		;;
	-h | --help)
		sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
		exit 0
		;;
	-*)
		echo "error: unknown option: $1" >&2
		exit 1
		;;
	*)
		OUTPUT="$1"
		shift
		;;
	esac
done

if [[ -n "$SESSION" && "$SESSION" != @* ]]; then
	SESSION="@${SESSION}"
fi

if ! command -v mcpc >/dev/null 2>&1; then
	echo "error: mcpc not found in PATH" >&2
	exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
	echo "error: jq not found in PATH" >&2
	exit 1
fi

if [[ -n "$SESSION" ]]; then
	if ! mcpc --json | jq -e --arg session "$SESSION" '.sessions[] | select(.name == $session)' >/dev/null; then
		echo "error: unknown mcpc session: ${SESSION}" >&2
		exit 1
	fi
fi

apply_skip_keys() {
	local tool_def="$1"
	if [[ ${#SKIP_KEYS[@]} -eq 0 ]]; then
		printf '%s' "$tool_def"
		return 0
	fi

	local del_paths=""
	local key
	for key in "${SKIP_KEYS[@]}"; do
		del_paths+=".${key}, "
	done
	del_paths="${del_paths%, }"
	jq "del(${del_paths})" <<<"$tool_def"
}

tools_out='[]'

while IFS= read -r session; do
	[[ -z "$session" ]] && continue

	session_info="$(mcpc --json "$session")"
	server_slug="${session#@}"

	while IFS= read -r tool; do
		[[ -z "$tool" ]] && continue
		if ! tool_def="$(mcpc --json "$session" tools-get "$tool" 2>/dev/null)"; then
			echo "warning: failed to fetch $session tools-get $tool" >&2
			continue
		fi
		if [[ -n "$server_slug" ]]; then
			prefixed_name="mcp__${server_slug}_${tool}"
			tool_def="$(echo "$tool_def" | jq --arg name "$prefixed_name" '.name = $name')"
		else
			echo "warning: empty session slug for ${session}; keeping tool name ${tool}" >&2
		fi
		tool_def="$(apply_skip_keys "$tool_def")"
		tools_out="$(jq -n --argjson arr "$tools_out" --argjson def "$tool_def" '$arr + [$def]')"
	done < <(echo "$session_info" | jq -r '.toolNames[]? // empty')
done < <(
	if [[ -n "$SESSION" ]]; then
		printf '%s\n' "$SESSION"
	else
		mcpc --json | jq -r '.sessions[].name'
	fi
)

jq -n --argjson tools "$tools_out" '{tools: $tools}' >"$OUTPUT"

tool_count="$(jq '.tools | length' "$OUTPUT")"
echo "Wrote ${tool_count} tools to ${OUTPUT}"
