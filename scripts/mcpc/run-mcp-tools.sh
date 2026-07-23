#!/usr/bin/env bash
# Smoke-call every tool in every mcpc session using payloads from scripts/mcpc/payloads/.
#
# Usage:
#   ./scripts/mcpc/run-mcp-tools.sh              # all sessions, all tools
#   ./scripts/mcpc/run-mcp-tools.sh @fff         # one session
#   ./scripts/mcpc/run-mcp-tools.sh @fff multi_grep
#   ./scripts/mcpc/run-mcp-tools.sh --dry-run
#   ./scripts/mcpc/run-mcp-tools.sh --output-dir ./scripts/mcpc/output/my-run
#
# Regenerate payloads:
#   ./scripts/mcpc/generate-tool-payloads.sh
#
# Each call writes:
#   <output>/<session>/<tool>.out  — metadata + text output (mcpc without --json)
#   <output>/<session>/<tool>.json — JSON output (mcpc --json)
#
# Payload source:
#   scripts/mcpc/payloads/<session>/<tool>.json

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOADS_DIR="${PAYLOADS_DIR:-${SCRIPT_DIR}/payloads}"
CWD="${CWD:-$(pwd -P)}"
DRY_RUN=0
SESSION_FILTER=""
TOOL_FILTER=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--output-dir)
		OUTPUT_DIR="${2:-}"
		shift 2
		;;
	@*)
		SESSION_FILTER="$1"
		shift
		;;
	*)
		TOOL_FILTER="$1"
		shift
		;;
	esac
done

OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/output/run-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"
SUMMARY_FILE="${OUTPUT_DIR}/summary.tsv"
printf 'session\ttool\texit_text\texit_json\tpayload\tout\tjson\n' >"$SUMMARY_FILE"

if ! command -v mcpc >/dev/null 2>&1; then
	echo "error: mcpc not found in PATH" >&2
	exit 1
fi
if ! command -v jq >/dev/null 2>&1; then
	echo "error: jq not found in PATH" >&2
	exit 1
fi
if [[ ! -d "$PAYLOADS_DIR" ]]; then
	echo "error: payloads dir not found: $PAYLOADS_DIR" >&2
	echo "hint: run ./scripts/mcpc/generate-tool-payloads.sh" >&2
	exit 1
fi

load_payload() {
	local session="$1"
	local tool="$2"
	local payload_file="${PAYLOADS_DIR}/${session#@}/${tool}.json"

	if [[ ! -f "$payload_file" ]]; then
		echo "SKIP  $session  $tool  (missing payload: ${payload_file#"$SCRIPT_DIR"/})" >&2
		return 1
	fi
	if jq -e '._smoke_skip == true' "$payload_file" >/dev/null 2>&1; then
		local reason
		reason="$(jq -r '._smoke_skip_reason // "marked _smoke_skip"' "$payload_file")"
		echo "SKIP  $session  $tool  ($reason)" >&2
		return 1
	fi
	jq -c 'del(._smoke_skip, ._smoke_skip_reason)' "$payload_file"
}

pass=0
fail=0
skip=0

save_tool_output() {
	local session="$1"
	local tool="$2"
	local payload_file_rel="$3"
	local payload_oneline="$4"
	local text_cmd_line="$5"
	local json_cmd_line="$6"
	local rc_text="$7"
	local rc_json="$8"
	local text_body="$9"
	local json_body="${10}"

	local session_dir="${OUTPUT_DIR}/${session#@}"
	mkdir -p "$session_dir"
	local out_file="${session_dir}/${tool}.out"
	local json_file="${session_dir}/${tool}.json"
	local out_rel="${session#@}/${tool}.out"
	local json_rel="${session#@}/${tool}.json"

	if [[ -n "$json_body" ]]; then
		if jq . <<<"$json_body" >"$json_file" 2>/dev/null; then
			:
		else
			jq -n --arg raw "$json_body" '{parseError: true, raw: $raw}' >"$json_file"
		fi
	else
		jq -n --arg rc "$rc_json" '{emptyResponse: true, exit: ($rc | tonumber? // $rc)}' >"$json_file"
	fi

	{
		echo "# payload file"
		echo "$payload_file_rel"
		echo
		echo "# command (text)"
		echo "$text_cmd_line"
		echo
		echo "# command (json)"
		echo "$json_cmd_line"
		echo
		echo "# payload"
		echo "$payload_oneline"
		echo
		echo "# exit text: $rc_text"
		echo "# exit json: $rc_json"
		echo "# json: $json_rel"
		echo
		echo "--- output ---"
		printf '%s\n' "$text_body"
	} >"$out_file"

	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$session" "$tool" "$rc_text" "$rc_json" "$payload_file_rel" "$out_rel" "$json_rel" >>"$SUMMARY_FILE"
	echo "saved: $out_file"
	echo "saved: $json_file"
}

run_mcpc_call() {
	local -a cmd=("$@")
	local tmp_out rc body

	tmp_out="$(mktemp)"
	set +e
	printf '%s\n' "$payload_oneline" | "${cmd[@]}" >"$tmp_out" 2>&1
	rc=$?
	set -e
	body="$(<"$tmp_out")"
	rm -f "$tmp_out"

	MCP_CALL_RC=$rc
	MCP_CALL_BODY=$body
}

while IFS= read -r session; do
	[[ -z "$session" ]] && continue
	[[ -n "$SESSION_FILTER" && "$session" != "$SESSION_FILTER" ]] && continue

	while IFS= read -r tool; do
		[[ -z "$tool" ]] && continue
		[[ -n "$TOOL_FILTER" && "$tool" != "$TOOL_FILTER" ]] && continue

		payload_file="${PAYLOADS_DIR}/${session#@}/${tool}.json"
		payload_file_rel="${payload_file#"$SCRIPT_DIR"/}"
		if ! payload_oneline="$(load_payload "$session" "$tool")"; then
			skip=$((skip + 1))
			continue
		fi

		payload_quoted="$(printf '%s' "$payload_oneline" | sed "s/'/'\\\\''/g")"
		text_cmd=(mcpc "$session" tools-call "$tool")
		json_cmd=(mcpc --json "$session" tools-call "$tool")
		text_cmd_line="echo '${payload_quoted}' | ${text_cmd[*]}"
		json_cmd_line="echo '${payload_quoted}' | ${json_cmd[*]}"

		echo "=== $session  $tool ==="
		echo "$payload_file_rel"
		echo "$text_cmd_line"
		echo "$json_cmd_line"

		if [[ "$DRY_RUN" -eq 1 ]]; then
			save_tool_output "$session" "$tool" "$payload_file_rel" "$payload_oneline" \
				"$text_cmd_line" "$json_cmd_line" "dry-run" "dry-run" \
				"(not executed)" '{"dryRun":true}'
			skip=$((skip + 1))
			continue
		fi

		run_mcpc_call "${text_cmd[@]}"
		rc_text=$MCP_CALL_RC
		text_body="$MCP_CALL_BODY"

		run_mcpc_call "${json_cmd[@]}"
		rc_json=$MCP_CALL_RC
		json_body="$MCP_CALL_BODY"

		save_tool_output "$session" "$tool" "$payload_file_rel" "$payload_oneline" \
			"$text_cmd_line" "$json_cmd_line" "$rc_text" "$rc_json" "$text_body" "$json_body"

		if [[ "$rc_text" -eq 0 && "$rc_json" -eq 0 ]]; then
			pass=$((pass + 1))
		else
			echo "FAIL  $session  $tool  (text=$rc_text json=$rc_json)" >&2
			fail=$((fail + 1))
		fi
		echo
	done < <(mcpc --json "$session" tools-list 2>/dev/null | jq -r '.[].name // empty')
done < <(mcpc --json | jq -r '.sessions[].name // empty')

echo "Done: pass=$pass fail=$fail skip=$skip"
echo "Payloads: $PAYLOADS_DIR"
echo "Output: $OUTPUT_DIR"
echo "Summary: $SUMMARY_FILE"
[[ "$fail" -eq 0 ]]
