#!/usr/bin/env bash
# Export all tool definitions from live mcpc sessions into a single JSON file.
#
# Usage:
#   ./scripts/mcpc/export-mcp-definitions.sh [output.json]
#
# Default output: output/mcp-definitions.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT="${1:-${SCRIPT_DIR}/output/mcp-definitions.json}"

if ! command -v mcpc >/dev/null 2>&1; then
	echo "error: mcpc not found in PATH" >&2
	exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
	echo "error: jq not found in PATH" >&2
	exit 1
fi

tools_out='[]'

while IFS= read -r session; do
	[[ -z "$session" ]] && continue

	session_info="$(mcpc --json "$session")"

	while IFS= read -r tool; do
		[[ -z "$tool" ]] && continue
		if ! tool_def="$(mcpc --json "$session" tools-get "$tool" 2>/dev/null)"; then
			echo "warning: failed to fetch $session tools-get $tool" >&2
			continue
		fi
		tools_out="$(jq -n --argjson arr "$tools_out" --argjson def "$tool_def" '$arr + [$def]')"
	done < <(echo "$session_info" | jq -r '.toolNames[]? // empty')
done < <(mcpc --json | jq -r '.sessions[].name')

jq -n --argjson tools "$tools_out" '{tools: $tools}' >"$OUTPUT"

tool_count="$(jq '.tools | length' "$OUTPUT")"
echo "Wrote ${tool_count} tools to ${OUTPUT}"
