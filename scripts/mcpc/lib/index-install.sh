#!/usr/bin/env bash
# One-time installers for local-search / MCP tooling (see index-install.sh).

INSTALL_DRY_RUN="${INSTALL_DRY_RUN:-0}"
INSTALL_SKIP_MCPC="${INSTALL_SKIP_MCPC:-0}"
INSTALL_WITH_DBHUB="${INSTALL_WITH_DBHUB:-0}"
INSTALL_FAIL=0

install_step() {
	local label="$1"
	shift
	echo
	echo "=== $label ==="
	if [[ "$INSTALL_DRY_RUN" -eq 1 ]]; then
		printf 'DRY   '
		printf '%q ' "$@"
		echo
		return 0
	fi
	if "$@"; then
		echo "OK  INSTALL  $label"
		return 0
	fi
	echo "FAIL  INSTALL  $label (exit $?)" >&2
	INSTALL_FAIL=$((INSTALL_FAIL + 1))
	return 1
}

install_step_optional() {
	local label="$1"
	shift
	local cmd="$1"
	if [[ "$INSTALL_DRY_RUN" -eq 1 ]]; then
		install_step "$label" "$@"
		return 0
	fi
	if ! command -v "$cmd" >/dev/null 2>&1; then
		echo "SKIP  INSTALL  $label  ($cmd not found)"
		return 0
	fi
	install_step "$label" "$@" || true
}

install_curl_bash() {
	local label="$1"
	local url="$2"
	shift 2
	if [[ "$INSTALL_DRY_RUN" -eq 1 ]]; then
		echo
		echo "=== $label ==="
		printf 'DRY   curl -fsSL %q | bash -s --' "$url"
		printf ' %q' "$@"
		echo
		return 0
	fi
	if curl -fsSL "$url" | bash -s -- "$@"; then
		echo "OK  INSTALL  $label"
		return 0
	fi
	echo "FAIL  INSTALL  $label (exit $?)" >&2
	INSTALL_FAIL=$((INSTALL_FAIL + 1))
	return 1
}

install_npm_global() {
	local label="$1"
	shift
	install_step_optional "$label" npm install -g "$@"
}

install_uv_tool() {
	local label="$1"
	local spec="$2"
	install_step_optional "$label" uv tool install "$spec"
}

ensure_gitignore_entries() {
	local gitignore="${REPO_ROOT}/.gitignore"
	local -a entries=("graphify-out/" ".codebase-memory/")
	local entry
	for entry in "${entries[@]}"; do
		if [[ ! -f "$gitignore" ]]; then
			if [[ "$INSTALL_DRY_RUN" -eq 1 ]]; then
				echo "DRY   append $entry to .gitignore"
			else
				printf '%s\n' "$entry" >>"$gitignore"
				echo "OK  append $entry to .gitignore"
			fi
			continue
		fi
		if grep -qxF "$entry" "$gitignore" 2>/dev/null || grep -qxF "${entry%/}" "$gitignore" 2>/dev/null; then
			echo "OK  .gitignore already has $entry"
		elif [[ "$INSTALL_DRY_RUN" -eq 1 ]]; then
			echo "DRY   append $entry to .gitignore"
		else
			printf '%s\n' "$entry" >>"$gitignore"
			echo "OK  append $entry to .gitignore"
		fi
	done
}

run_mcpc_connects() {
	if [[ "$INSTALL_SKIP_MCPC" -eq 1 ]]; then
		echo "SKIP  MCPC connects (--skip-mcpc)"
		return 0
	fi
	install_step_optional "mcpc connect coolgrep-skill" mcpc connect \
		https://mcp.skillsovermcp.com/mcp/lightonai/next-plaid @coolgrep-skill || true
	install_step_optional "mcpc connect context7" mcpc connect https://mcp.context7.com/mcp || true
	install_step_optional "mcpc connect deepwiki" mcpc connect https://mcp.deepwiki.com/mcp || true
	local stdio_cfg="${HOME}/.mcpc/servers-stdio.json"
	if [[ -f "$stdio_cfg" ]]; then
		install_step_optional "mcpc connect stdio servers" mcpc connect "$stdio_cfg" --stdio || true
	else
		echo "SKIP  mcpc connect stdio  ($stdio_cfg not found)"
	fi
}

run_local_search_install() {
	INSTALL_FAIL=0

	echo "Repo root: $REPO_ROOT"
	echo "Install dry-run: $INSTALL_DRY_RUN"
	echo

	echo "=== Package managers (brew) ==="
	install_step_optional "brew colgrep" brew install lightonai/tap/colgrep || true
	install_step_optional "brew rtk" brew install rtk || true

	echo
	echo "=== curl installers ==="
	install_curl_bash "codebase-memory-mcp" \
		https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh \
		--ui --skip-config true || true
	install_curl_bash "fff-mcp" https://dmtrkovalenko.dev/install-fff-mcp.sh || true

	echo
	echo "=== npm global ==="
	install_npm_global "context-mode" context-mode || true
	install_npm_global "codegraph" @colbymchenry/codegraph || true
	install_npm_global "executor" executor || true
	install_npm_global "gitnexus" \
		--allow-scripts=gitnexus,@ladybugdb/core,@scarf/scarf,onnxruntime-node,tree-sitter,tree-sitter-c-sharp,tree-sitter-cpp,tree-sitter-go,tree-sitter-java,tree-sitter-javascript,tree-sitter-php,tree-sitter-python,tree-sitter-ruby,tree-sitter-rust,tree-sitter-typescript,sharp,protobufjs \
		gitnexus || true

	echo
	echo "=== uv tool ==="
	install_uv_tool "jcodemunch-mcp" "jcodemunch-mcp[all]" || true
	install_uv_tool "graphify" "graphifyy[all]" || true
	install_uv_tool "semble" semble || true
	install_uv_tool "code-review-graph" code-review-graph || true

	echo
	echo "=== cargo install ==="
	install_step_optional "hedl-mcp" cargo install hedl-mcp || true
	install_step_optional "lean-ctx" cargo install lean-ctx || false

	echo
	echo "=== Tool setup / registration ==="
	install_step_optional "codebase-memory-mcp update" codebase-memory-mcp update || true
	install_step_optional "jcodemunch-mcp init" jcodemunch-mcp init || true
	install_step_optional "colgrep codex skill" colgrep --install-codex || true
	install_step_optional "codegraph init" codegraph init || true
	install_step_optional "graphify install" graphify install || true
	install_step_optional "graphify cursor install" graphify cursor install || true
	install_step_optional "graphify hook install" graphify hook install || true
	install_step_optional "semble install" semble install || true
	install_step_optional "semble cursor mcp subagent" semble install --agent cursor --type mcp subagent --yes || true
	install_step_optional "executor service" executor install || true
	install_step_optional "gitnexus setup" npx gitnexus setup || true
	install_step_optional "lean-ctx init" lean-ctx init --agent cursor || false
	install_step_optional "lean-ctx gain" lean-ctx gain || false
	install_step_optional "lean-ctx tools power" lean-ctx tools power || false
	install_step_optional "rtk gain" rtk gain || true
	install_step_optional "rtk init cursor" rtk init -g --agent cursor || true

	echo
	echo "=== MCPC remote / stdio sessions ==="
	run_mcpc_connects

	if [[ "$INSTALL_WITH_DBHUB" -eq 1 ]]; then
		echo
		echo "=== dbhub (optional) ==="
		install_npm_global "dbhub" @bytebase/dbhub@0.23.0 || true
		echo "NOTE: configure ~/.dbhub/config.toml and start dbhub manually with your DSN." >&2
	else
		echo
		echo "SKIP  dbhub  (pass --with-dbhub to install npm package; configure DSN manually)"
	fi

	echo
	echo "=== Repo gitignore ==="
	ensure_gitignore_entries

	echo
	echo "SKIP  token-optimizer  (no Cursor support yet)"
	echo "SKIP  headroom  (no install command documented)"
	echo "NOTE: Run ./scripts/mcpc/index-init.sh after install to build indexes." >&2
	echo "NOTE: executor/dbhub MCP URLs need manual token/DSN setup." >&2
	echo "NOTE: context-mode Cursor hooks are configured separately in Cursor settings." >&2

	echo
	echo "Done: install_fail=$INSTALL_FAIL"
	return "$INSTALL_FAIL"
}
