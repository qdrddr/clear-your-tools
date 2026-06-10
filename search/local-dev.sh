#!/usr/bin/env bash
# Local monorepo workflow: Rust crate → SDK artifacts → src/cyt app (no registry publish).
#
# The main app (./src/cyt/) uses cyt-indexer-sdk from sdk/python via pyproject.toml:
#   [tool.uv.sources]
#   cyt-indexer-sdk = { path = "sdk/python", editable = true }
# Production installs (pip install clear-your-tools) pull cyt-indexer-sdk from PyPI instead.
#
# Usage:
#   ./search/local-dev.sh <command> [args...]
#
# Commands:
#   setup              uv sync workspace (local SDK source override)
#   rust               cargo test -p cyt-indexer + release CLI catalog build
#   indexer [subcmd]   cyt-indexer build tools|skills / retrieve tools from debug/full_example.json
#                      subcmd: build [tools|skills] | survivors | retrieve | all (default: all)
#                      env: CYT_CATALOG_DIR, CYT_INDEXER_SYSTEM_POLICY, CYT_INDEXER_MCP_POLICY,
#                           CYT_INDEXER_TOOL_POLICIES (default: AskUserQuestion=always_include)
#   sdk-python         maturin develop --release
#   sdk-typescript     npm ci, build, test
#   verify             assert SDK is local editable + native import works
#   test               verify + pytest src/tests
#   build-wheels       uv build clear-your-tools wheel/sdist
#   proxy [args...]    verify + uv run src/cyt/proxy/cli.py proxy ...
#                      API keys: use env if set, else ${REPO}/.env or ~/.config/cyt/.env,
#                      else macOS Keychain (search/proxy.sh)
#   simulate-registry  isolated venv: install built wheels + cargo/npm dry-run checks
#   all                setup → rust → sdk-python → verify → test → build-wheels
#   ci                 setup → verify → ruff-check → pytest → build-wheels (no rust/npm)
#
# Examples:
#   ./search/local-dev.sh all
#   ./search/local-dev.sh proxy --port 8834
#   KEEP_SIM_DIR=1 ./search/local-dev.sh simulate-registry
set -euo pipefail

# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/local-dev-lib.sh"

usage() {
	sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
}

cmd="${1:-}"
shift || true

case "${cmd}" in
setup)
	require_repo_root
	cyt_sync_workspace
	;;
rust)
	require_repo_root
	cyt_build_rust
	;;
indexer)
	require_repo_root
	require_cmd jq
	sub="${1:-all}"
	shift || true
	case "${sub}" in
	build)
		target="${1:-tools}"
		shift || true
		case "${target}" in
		tools)
			cyt_indexer_build_catalog
			;;
		skills)
			cyt_indexer_build_skills "$@"
			;;
		*)
			die "unknown build target: ${target} (try: tools, skills)"
			;;
		esac
		;;
	survivors)
		cyt_indexer_extract_survivors
		;;
	retrieve)
		cyt_indexer_retrieve "$@"
		;;
	all)
		cyt_indexer_all "$@"
		;;
	-h | --help | help)
		cat <<EOF
Usage: ./search/local-dev.sh indexer [build|survivors|retrieve|all] [args...]

  build tools   jq '.body.tools' debug/full_example.json -> cyt-indexer build tools -> .catalog/
  build skills  cyt-indexer build skills --skills DIR [--output DIR]
  survivors     jq rerank json/md -> .catalog/survivors.json (scores as numbers)
  retrieve      cyt-indexer retrieve tools with default policies (score filter off for rerank survivors)
  all           build tools + survivors + retrieve (default)

Retrieve defaults:
  --system-policy prune_optional
  --mcp-policy prune_all
  --tool-policy AskUserQuestion=always_include

Override via env:
  CYT_CATALOG_DIR  CYT_EXAMPLE_JSON  CYT_SURVIVORS_JSON  CYT_RETRIEVE_OUT
  CYT_INDEXER_SYSTEM_POLICY  CYT_INDEXER_MCP_POLICY
  CYT_INDEXER_TOOL_POLICIES='AskUserQuestion=always_include Agent=prune_optional'

Examples:
  ./search/local-dev.sh indexer
  ./search/local-dev.sh indexer build
  ./search/local-dev.sh indexer build skills --skills ~/.claude/skills --output ./.catalog
  ./search/local-dev.sh indexer retrieve --tool-policy Bash=always_include
EOF
		;;
	*)
		die "unknown indexer subcommand: ${sub} (try: build, survivors, retrieve, all)"
		;;
	esac
	;;
sdk-python)
	require_repo_root
	cyt_sync_workspace
	cyt_build_sdk_python
	cyt_verify_sdk_import
	;;
sdk-typescript)
	require_repo_root
	cyt_build_sdk_typescript
	;;
verify)
	require_repo_root
	cyt_verify_local_sdk
	;;
test)
	require_repo_root
	cyt_test_app
	;;
build-wheels)
	require_repo_root
	cyt_sync_workspace
	cyt_verify_local_sdk
	cyt_build_app_wheel
	;;
proxy)
	require_repo_root
	cyt_run_proxy "$@"
	;;
simulate-registry)
	require_repo_root
	require_cmd uv
	require_cmd cargo
	require_cmd npm
	cyt_sync_workspace
	cyt_build_sdk_python
	cyt_build_rust

	SIM_DIR="${CYT_SIM_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/cyt-local-dev.XXXXXX")}"
	KEEP_SIM_DIR="${KEEP_SIM_DIR:-}"
	trap '[[ -n "${KEEP_SIM_DIR}" ]] || rm -rf "${SIM_DIR}"' EXIT

	info "simulate registry install in ${SIM_DIR}"
	mkdir -p "${SIM_DIR}/dist-sdk" "${SIM_DIR}/dist-app" "${SIM_DIR}/npm-pack"

	info "build cyt-indexer-sdk wheel"
	(cd "${CYT_REPO_ROOT}/sdk/python" && uv build -o "${SIM_DIR}/dist-sdk")

	info "build clear-your-tools wheel"
	(cd "${CYT_REPO_ROOT}" && uv build -o "${SIM_DIR}/dist-app")

	info "cargo publish --dry-run (cyt-indexer)"
	(cd "${CYT_REPO_ROOT}" && cargo publish -p cyt-indexer --dry-run)

	info "npm pack (TypeScript SDK; requires native .node)"
	(cd "${CYT_REPO_ROOT}/sdk/typescript" && npm ci && npm run build && npm pack --pack-destination "${SIM_DIR}/npm-pack")

	SIM_VENV="${SIM_DIR}/venv"
	uv venv "${SIM_VENV}"
	# shellcheck disable=SC1091
	source "${SIM_VENV}/bin/activate"
	info "uv pip install local wheels (no pyproject [tool.uv.sources] in this venv)"
	SDK_WHL=("${SIM_DIR}"/dist-sdk/cyt_indexer_sdk-*.whl)
	APP_WHL=("${SIM_DIR}"/dist-app/clear_your_tools-*.whl)
	[[ -f "${SDK_WHL[0]}" ]] || die "SDK wheel not found under ${SIM_DIR}/dist-sdk"
	[[ -f "${APP_WHL[0]}" ]] || die "app wheel not found under ${SIM_DIR}/dist-app"
	uv pip install "${SDK_WHL[0]}"
	# Typical PyPI install with optional proxy/pruner deps (not the monorepo path override).
	uv pip install "${APP_WHL[0]}[all]"

	info "smoke imports in isolated venv (registry-style wheels, not editable sdk/python)"
	python - <<'PY'
from importlib import metadata

from cyt_indexer._native import build_catalog_index as native_build
from cyt_indexer.build import build_catalog_index as sdk_build
from cyt.indexer.build import build_catalog_index as app_build
import cyt

assert callable(native_build)
assert sdk_build is app_build
assert metadata.version("cyt-indexer-sdk") == metadata.version("clear-your-tools")
print("OK: isolated wheel install")
print("  cyt version:", getattr(cyt, "__version__", "?"))
print("  cyt-indexer-sdk:", metadata.version("cyt-indexer-sdk"))
PY

	deactivate 2>/dev/null || true

	info "simulate-registry done"
	echo "  SDK wheels:    ${SIM_DIR}/dist-sdk"
	echo "  App wheels:    ${SIM_DIR}/dist-app"
	echo "  npm tarball:   ${SIM_DIR}/npm-pack"
	echo "  test venv:     ${SIM_VENV}"
	if [[ -n "${KEEP_SIM_DIR}" ]]; then
		trap - EXIT
		echo "  (KEEP_SIM_DIR=1 — directory kept)"
	fi
	;;
all)
	require_repo_root
	cyt_sync_workspace
	cyt_build_rust
	cyt_build_sdk_python
	cyt_verify_local_sdk
	cyt_test_app
	cyt_build_app_wheel
	info "all done (run ./search/local-dev.sh proxy --port 8834 for manual proxy)"
	;;
ci)
	require_repo_root
	cyt_sync_workspace
	cyt_verify_local_sdk
	cyt_verify_sdk_import
	if command -v ruff >/dev/null 2>&1 || [[ -x "${CYT_VENV_BIN}/ruff" ]]; then
		info "ruff check"
		uv run ruff check src/cyt src/tests sdk/python
	else
		info "skip ruff (not on PATH)"
	fi
	cyt_test_app
	cyt_build_app_wheel
	;;
"" | -h | --help | help)
	usage
	;;
*)
	echo "unknown command: ${cmd}" >&2
	echo >&2
	usage >&2
	exit 1
	;;
esac
