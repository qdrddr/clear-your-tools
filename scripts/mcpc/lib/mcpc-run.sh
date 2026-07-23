#!/usr/bin/env bash
# Shared helpers for MCPC index scripts.

if [[ -z "${MCPC_SCRIPT_DIR:-}" ]]; then
	MCPC_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
export MCPC_SCRIPT_DIR

mcpc_run_require_tools() {
	if ! command -v mcpc >/dev/null 2>&1; then
		echo "error: mcpc not found in PATH" >&2
		return 1
	fi
	if ! command -v jq >/dev/null 2>&1; then
		echo "error: jq not found in PATH" >&2
		return 1
	fi
}

resolve_repo_root() {
	local start="${1:-$(pwd -P)}"
	if REPO_ROOT="$(git -C "$start" rev-parse --show-toplevel 2>/dev/null)"; then
		REPO_ROOT="$(cd "$REPO_ROOT" && pwd -P)"
	else
		REPO_ROOT="$(cd "$start" && pwd -P)"
	fi
	export REPO_ROOT
}

parse_mcp_json_text() {
	local raw="$1"
	local parsed
	parsed="$(jq -r '
		if .structuredContent then .structuredContent
		elif (.content | type) == "array" and (.content[0].text // null) then
			(.content[0].text | try fromjson catch .)
		else empty end
	' <<<"$raw" 2>/dev/null || true)"
	if [[ -z "$parsed" || "$parsed" == "null" ]]; then
		return 0
	fi
	if jq -e . >/dev/null 2>&1 <<<"$parsed"; then
		printf '%s' "$parsed"
		return 0
	fi
	# Some MCP tools append markdown after embedded JSON in content[0].text.
	jq -n --arg text "$parsed" '
		($text | split("\n---")[0] | try fromjson catch empty) // empty
	' 2>/dev/null || true
}

resolve_jcodemunch_repo() {
	local raw parsed
	JM_REPO=""
	raw="$(mcpc --json @jcodemunch tools-call resolve_repo 2>/dev/null <<<"{\"path\":\"${REPO_ROOT}\"}")" || return 0
	parsed="$(parse_mcp_json_text "$raw")"
	if [[ -n "$parsed" && "$parsed" != "null" ]]; then
		JM_REPO="$(jq -r '.repo // empty' <<<"$parsed")"
	fi
	export JM_REPO
}

resolve_codebase_project() {
	local raw parsed
	CB_PROJECT=""
	raw="$(mcpc --json @codebase-memory tools-call list_projects 2>/dev/null <<<"{}")" || return 0
	parsed="$(parse_mcp_json_text "$raw")"
	if [[ -z "$parsed" || "$parsed" == "null" ]]; then
		return 0
	fi
	CB_PROJECT="$(jq -r --arg root "$REPO_ROOT" '
		.projects[]?
		| .root_path as $r
		| select($root == $r or ($root | startswith($r + "/")))
		| {name: (.name // .project), n: ($r | length)}
	' <<<"$parsed" | jq -s 'sort_by(.n) | last | .name // empty')"
	export CB_PROJECT
}

resolve_gitnexus_repo() {
	local raw parsed names
	GITNEXUS_REPO="clear-your-tools"
	raw="$(mcpc --json @gitnexus tools-call list_repos 2>/dev/null <<<"{}")" || return 0
	parsed="$(parse_mcp_json_text "$raw")"
	if [[ -z "$parsed" || "$parsed" == "null" ]]; then
		export GITNEXUS_REPO
		return 0
	fi
	names="$(jq -r '.repositories[]?.name // empty' <<<"$parsed")"
	for preferred in clear-your-tools chunk-your-tools; do
		if grep -qx "$preferred" <<<"$names"; then
			GITNEXUS_REPO="$preferred"
			break
		fi
	done
	export GITNEXUS_REPO
}

resolve_index_context() {
	resolve_repo_root "${1:-$(pwd -P)}"
	resolve_jcodemunch_repo
	resolve_codebase_project
	resolve_gitnexus_repo
	CHANGED_FILES_JSON="${CHANGED_FILES_JSON:-[]}"
	export CHANGED_FILES_JSON
}

substitute_payload() {
	local template="$1"
	jq -c \
		--arg root "$REPO_ROOT" \
		--arg repo "${JM_REPO:-}" \
		--arg project "${CB_PROJECT:-}" \
		--arg gn "${GITNEXUS_REPO:-clear-your-tools}" \
		--argjson files "${CHANGED_FILES_JSON:-[]}" \
		'
		walk(
			if type == "string" then
				if . == "{{repo_root}}" then $root
				elif . == "{{repo}}" then $repo
				elif . == "{{project}}" then $project
				elif . == "{{gitnexus_repo}}" then $gn
				else .
				end
			else .
			end
		)
		| if .file_paths == ["{{changed_files}}"] then .file_paths = $files else . end
		| if .changed_files == ["{{changed_files}}"] then .changed_files = $files else . end
		' "$template"
}

load_manifest_payload() {
	local session="$1"
	local tool="$2"
	local payloads_dir="$3"
	local payload_file="${payloads_dir}/${session#@}/${tool}.json"

	if [[ ! -f "$payload_file" ]]; then
		echo "error: missing payload: $payload_file" >&2
		return 1
	fi
	substitute_payload "$payload_file"
}

call_mcpc_tool() {
	local session="$1"
	local tool="$2"
	local payload_oneline="$3"
	local tmp_out

	tmp_out="$(mktemp)"
	set +e
	printf '%s\n' "$payload_oneline" | mcpc "$session" tools-call "$tool" >"$tmp_out" 2>&1
	MCP_CALL_RC_TEXT=$?
	set -e
	MCP_CALL_BODY_TEXT="$(<"$tmp_out")"
	rm -f "$tmp_out"

	tmp_out="$(mktemp)"
	set +e
	printf '%s\n' "$payload_oneline" | mcpc --json "$session" tools-call "$tool" >"$tmp_out" 2>&1
	MCP_CALL_RC_JSON=$?
	set -e
	MCP_CALL_BODY_JSON="$(<"$tmp_out")"
	rm -f "$tmp_out"
}

save_index_tool_output() {
	local session="$1"
	local tool="$2"
	local payload_file_rel="$3"
	local payload_oneline="$4"
	local rc_text="$5"
	local rc_json="$6"
	local text_body="$7"
	local json_body="$8"

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
		echo "# payload"
		echo "$payload_oneline"
		echo
		echo "# exit text: $rc_text"
		echo "# exit json: $rc_json"
		echo
		echo "--- output ---"
		printf '%s\n' "$text_body"
	} >"$out_file"

	printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$session" "$tool" "$rc_text" "$rc_json" "$out_rel" "$json_rel" >>"$SUMMARY_FILE"
}

run_manifest() {
	local manifest_file="$1"
	local payloads_dir="$2"
	local dry_run="${3:-0}"

	MANIFEST_PASS=0
	MANIFEST_FAIL=0
	MANIFEST_SKIP=0

	while IFS=$'\t' read -r session tool optional; do
		[[ -z "$session" || "$session" == \#* ]] && continue
		[[ -z "$tool" ]] && continue

		if [[ "$optional" == "optional" && "$CHANGED_FILES_JSON" == "[]" ]]; then
			echo "SKIP  $session  $tool  (no changed files)"
			MANIFEST_SKIP=$((MANIFEST_SKIP + 1))
			continue
		fi

		local payload_file="${payloads_dir}/${session#@}/${tool}.json"
		local payload_file_rel="${payload_file#"${MCPC_SCRIPT_DIR:-}"\/}"
		local payload_oneline
		if ! payload_oneline="$(load_manifest_payload "$session" "$tool" "$payloads_dir")"; then
			MANIFEST_FAIL=$((MANIFEST_FAIL + 1))
			continue
		fi

		echo "=== $session  $tool ==="
		echo "$payload_oneline"

		if [[ "$dry_run" -eq 1 ]]; then
			save_index_tool_output "$session" "$tool" "$payload_file_rel" "$payload_oneline" \
				"dry-run" "dry-run" "(not executed)" '{"dryRun":true}'
			MANIFEST_SKIP=$((MANIFEST_SKIP + 1))
			continue
		fi

		call_mcpc_tool "$session" "$tool" "$payload_oneline"
		save_index_tool_output "$session" "$tool" "$payload_file_rel" "$payload_oneline" \
			"$MCP_CALL_RC_TEXT" "$MCP_CALL_RC_JSON" "$MCP_CALL_BODY_TEXT" "$MCP_CALL_BODY_JSON"

		if [[ "$MCP_CALL_RC_TEXT" -eq 0 && "$MCP_CALL_RC_JSON" -eq 0 ]]; then
			MANIFEST_PASS=$((MANIFEST_PASS + 1))
		else
			echo "FAIL  $session  $tool  (text=$MCP_CALL_RC_TEXT json=$MCP_CALL_RC_JSON)" >&2
			MANIFEST_FAIL=$((MANIFEST_FAIL + 1))
		fi
		echo
	done <"$manifest_file"
}

run_cli_if_available() {
	local label="$1"
	shift
	if "$@"; then
		echo "OK  CLI  $label"
		return 0
	fi
	echo "FAIL  CLI  $label  (exit $?)" >&2
	return 1
}

run_cli_optional() {
	local label="$1"
	shift
	if [[ $# -eq 0 ]]; then
		return 0
	fi
	local cmd="$1"
	if ! command -v "$cmd" >/dev/null 2>&1; then
		echo "SKIP  CLI  $label  ($cmd not found)"
		return 0
	fi
	run_cli_if_available "$label" "$@" || return 1
}

# CLI fallbacks aligned with ~/.cursor/skills/local-search/SKILL.md (Install/Indexing sections).
run_local_search_init_cli() {
	local cli_fail=0

	echo "=== local-search init CLI (upgrade/setup) ==="
	run_cli_optional "codebase-memory-mcp update" codebase-memory-mcp update || cli_fail=$((cli_fail + 1))
	run_cli_optional "jcodemunch-mcp init" jcodemunch-mcp init || cli_fail=$((cli_fail + 1))
	run_cli_optional "colgrep update" colgrep update || cli_fail=$((cli_fail + 1))
	if command -v uv >/dev/null 2>&1; then
		run_cli_optional "uv tool update graphify" uv tool update graphify || true
	fi
	run_cli_optional "codegraph upgrade" codegraph upgrade || cli_fail=$((cli_fail + 1))

	echo
	echo "=== local-search init CLI (index) ==="
	run_cli_optional "gitnexus index" npx gitnexus index || cli_fail=$((cli_fail + 1))
	run_cli_optional "gitnexus analyze" npx gitnexus analyze --index-only || cli_fail=$((cli_fail + 1))
	run_cli_optional "jcodemunch-mcp index" jcodemunch-mcp index "$REPO_ROOT" || cli_fail=$((cli_fail + 1))
	run_cli_optional "codebase-memory index_repository" \
		codebase-memory-mcp cli index_repository --repo-path "$REPO_ROOT" --mode full --persistence true ||
		cli_fail=$((cli_fail + 1))

	if command -v colgrep >/dev/null 2>&1; then
		echo "warning: colgrep init can be slow on large repos" >&2
		run_cli_optional "colgrep init" colgrep init -y . || cli_fail=$((cli_fail + 1))
		run_cli_optional "colgrep status" colgrep status || true
	else
		echo "SKIP  CLI  colgrep init  (colgrep not found)"
	fi

	if command -v codegraph >/dev/null 2>&1; then
		run_cli_optional "codegraph init" codegraph init || true
		if codegraph index -f 2>/dev/null; then
			echo "OK  CLI  codegraph index -f"
		elif codegraph sync 2>/dev/null; then
			echo "OK  CLI  codegraph sync"
		else
			echo "FAIL  CLI  codegraph index/sync" >&2
			cli_fail=$((cli_fail + 1))
		fi
	else
		echo "SKIP  CLI  codegraph  (codegraph not found)"
	fi

	if command -v graphify >/dev/null 2>&1; then
		run_cli_optional "graphify update" graphify update . || cli_fail=$((cli_fail + 1))
	else
		echo "SKIP  CLI  graphify update  (graphify not found)"
	fi

	echo "NOTE: graphify watch / jcodemunch-mcp watch are long-running daemons; start manually if desired." >&2
	return "$cli_fail"
}

run_local_search_update_cli() {
	local cli_fail=0

	echo "=== local-search update CLI ==="
	run_cli_optional "jcodemunch-mcp upgrade" jcodemunch-mcp upgrade || cli_fail=$((cli_fail + 1))
	run_cli_optional "jcodemunch-mcp index" jcodemunch-mcp index "$REPO_ROOT" || cli_fail=$((cli_fail + 1))

	if command -v npx >/dev/null 2>&1; then
		run_cli_if_available "gitnexus detect-changes" \
			npx gitnexus detect-changes -r "${GITNEXUS_REPO:-clear-your-tools}" || cli_fail=$((cli_fail + 1))
	else
		echo "SKIP  CLI  gitnexus detect-changes  (npx not found)"
	fi

	if command -v codegraph >/dev/null 2>&1; then
		run_cli_optional "codegraph sync" codegraph sync || cli_fail=$((cli_fail + 1))
	else
		echo "SKIP  CLI  codegraph sync  (codegraph not found)"
	fi

	if command -v graphify >/dev/null 2>&1; then
		run_cli_optional "graphify update" graphify update . || cli_fail=$((cli_fail + 1))
	else
		echo "SKIP  CLI  graphify update  (graphify not found)"
	fi

	return "$cli_fail"
}
