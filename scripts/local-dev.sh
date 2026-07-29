#!/usr/bin/env bash
# Local monorepo workflow: Rust core → SDK artifacts → main app (src/) (no registry publish).
#
# The main app (./src/cyt/) uses cyt-indexer-sdk from sdk/python via pyproject.toml:
#   [tool.uv.sources]
#   cyt-indexer-sdk = { path = "sdk/python", editable = true }
# Production installs (pip install clear-your-tools) pull cyt-indexer-sdk from PyPI instead.
#
# Usage:
#   ./scripts/local-dev.sh [--short|--silent] <command> [args...]
#
# Options:
#   --short | --silent   Only print error/warning lines (hide info/success noise)
#
# Commands:
#   Core (Rust):
#     core-rust | rust     cargo test -p cyt-indexer --features testing,ffi + release CLI catalog build
#     indexer [subcmd]     cyt-indexer build tools|skills / retrieve (see help)
#
#   SDKs:
#     sdk-python           maturin develop --release + verify sdk/python
#     sdk-verify           verify sdk/python install + native import
#     sdk-typescript       npm ci, build, test (sdk/typescript)
#     sdk-c                cmake build + ctest (sdk/c)
#     sdk-go               build C FFI + go test (sdk/go)
#     sdk-all              all SDK targets above
#
#   Main app (src/):
#     app-setup | setup    uv sync workspace (editable sdk/python via pyproject.toml)
#     app-verify           verify main app (src/) re-exports local cyt-indexer-sdk
#     app-test | test      app-verify + pytest unit/quality_metrics (integration excluded)
#     app-build | build-wheels
#                          uv build clear-your-tools wheel/sdist
#     app-all              app-setup → app-verify → app-test → app-build
#
#   Other:
#     proxy [args...]      verify + uv run src/cyt/proxy/cli.py proxy ...
#     simulate-registry    isolated venv: install built wheels + cargo/npm dry-run checks
#     ci                   app-setup → app-verify → ast-grep scan → import checks → ruff → pytest → app-build (no rust/other sdks)
#     all                  core-rust → all SDKs → app-all (full monorepo check)
#
# Examples:
#   ./scripts/local-dev.sh all
#   ./scripts/local-dev.sh --silent sdk-go
#   ./scripts/local-dev.sh sdk-go
#   ./scripts/local-dev.sh proxy --port 8834
#   KEEP_SIM_DIR=1 ./scripts/local-dev.sh simulate-registry
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/local-dev-lib.sh"
export SHORTEN_ROOT="${CYT_REPO_ROOT}"

CYT_LOCAL_DEV_SHORT="${CYT_LOCAL_DEV_SHORT:-}"
LOCAL_DEV_ARGS=()
while (($#)); do
	case "$1" in
	--short | --silent)
		CYT_LOCAL_DEV_SHORT=1
		shift
		;;
	*)
		LOCAL_DEV_ARGS+=("$1")
		shift
		;;
	esac
done
export CYT_LOCAL_DEV_SHORT

usage() {
	sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'
}

_cyt_local_dev_main() {
	local cmd="${1:-}"
	shift || true

	case "${cmd}" in
	core-rust | rust)
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
			target="${1:-tools}"
			if [[ "${target}" == --* ]]; then
				target="tools"
			else
				shift || true
			fi
			case "${target}" in
			tools)
				cyt_indexer_retrieve "$@"
				;;
			skills)
				cyt_indexer_retrieve_skills "$@"
				;;
			*)
				die "unknown retrieve target: ${target} (try: tools, skills)"
				;;
			esac
			;;
		all)
			cyt_indexer_all "$@"
			;;
		-h | --help | help)
			cat <<EOF
Usage: ./scripts/local-dev.sh indexer [build|survivors|retrieve|all] [args...]

  build tools   jq '.body.tools' debug/full_example.json -> cyt-indexer build tools -> .catalog/
  build skills  cyt-indexer build skills --skills DIR [--output DIR]
  survivors     jq rerank json/md -> .catalog/survivors.json (scores as numbers)
  retrieve tools   cyt-indexer retrieve tools with default policies (score filter off for rerank survivors)
  retrieve skills  cyt-indexer retrieve skills --catalog DIR --doc-id ID --query TYPE --output FILE
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
  ./scripts/local-dev.sh indexer
  ./scripts/local-dev.sh indexer build
  ./scripts/local-dev.sh indexer build skills --skills ~/.claude/skills --output ./.catalog
  ./scripts/local-dev.sh indexer retrieve --tool-policy Bash=always_include
  ./scripts/local-dev.sh indexer retrieve skills --catalog ./.catalog --doc-id lean-ctx__skill --query content --line_num 15
EOF
			;;
		*)
			die "unknown indexer subcommand: ${sub} (try: build, survivors, retrieve, all)"
			;;
		esac
		;;
	sdk-python)
		require_repo_root
		cyt_build_sdk_python
		cyt_verify_sdk_python
		;;
	sdk-verify)
		require_repo_root
		cyt_verify_sdk_python
		;;
	sdk-typescript)
		require_repo_root
		cyt_build_sdk_typescript
		;;
	sdk-c)
		require_repo_root
		cyt_build_sdk_c
		;;
	sdk-go)
		require_repo_root
		cyt_build_sdk_go
		;;
	sdk-all)
		require_repo_root
		cyt_section "SDK: Python"
		cyt_build_sdk_python
		cyt_verify_sdk_python
		cyt_section "SDK: C"
		cyt_build_sdk_c
		cyt_section "SDK: Go"
		cyt_build_sdk_go
		cyt_section "SDK: TypeScript"
		cyt_build_sdk_typescript
		;;
	app-setup | setup)
		require_repo_root
		cyt_sync_app
		;;
	app-verify)
		require_repo_root
		cyt_verify_app_python
		;;
	verify)
		require_repo_root
		cyt_verify_sdk_python
		cyt_verify_app_python
		;;
	app-test | test)
		require_repo_root
		cyt_test_app
		;;
	app-build | build-wheels)
		require_repo_root
		cyt_sync_app
		cyt_verify_app_python
		cyt_build_app_wheel
		;;
	app-all)
		require_repo_root
		cyt_build_all_app
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
		cyt_sync_app
		cyt_build_sdk_python
		cyt_build_rust

		SIM_DIR="${CYT_SIM_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/cyt-local-dev.XXXXXX")}"
		KEEP_SIM_DIR="${KEEP_SIM_DIR:-}"
		trap '[[ -n "${KEEP_SIM_DIR}" ]] || rm -rf "${SIM_DIR}"' EXIT

		info "simulate registry install"
		mkdir -p "${SIM_DIR}/dist-sdk" "${SIM_DIR}/dist-app" "${SIM_DIR}/npm-pack"

		info "build cyt-indexer-sdk wheel"
		cyt_run bash -c "cd \"${CYT_REPO_ROOT}/sdk/python\" && uv build -o \"${SIM_DIR}/dist-sdk\""

		info "build clear-your-tools wheel"
		cyt_run bash -c "cd \"${CYT_REPO_ROOT}\" && uv build -o \"${SIM_DIR}/dist-app\""

		info "cargo publish --dry-run"
		cyt_run bash -c "cd \"${CYT_REPO_ROOT}\" && cargo publish -p cyt-indexer --dry-run --allow-dirty"

		info "npm pack"
		cyt_run bash -c "cd \"${CYT_REPO_ROOT}/sdk/typescript\" && npm ci && npm run build && npm pack --pack-destination \"${SIM_DIR}/npm-pack\""

		SIM_VENV="${SIM_DIR}/venv"
		cyt_run uv venv "${SIM_VENV}"
		# shellcheck disable=SC1091
		source "${SIM_VENV}/bin/activate"
		info "install wheels in isolated venv"
		SDK_WHL=("${SIM_DIR}"/dist-sdk/cyt_indexer_sdk-*.whl)
		APP_WHL=("${SIM_DIR}"/dist-app/clear_your_tools-*.whl)
		[[ -f "${SDK_WHL[0]}" ]] || die "SDK wheel not found under ${SIM_DIR}/dist-sdk"
		[[ -f "${APP_WHL[0]}" ]] || die "app wheel not found under ${SIM_DIR}/dist-app"
		cyt_run uv pip install "${SDK_WHL[0]}"
		cyt_run uv pip install "${APP_WHL[0]}[all]"

		info "smoke imports"
		cyt_run python - <<'PY'
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

		info "simulate-registry done (${SIM_DIR})"
		if [[ -n "${KEEP_SIM_DIR}" ]]; then
			trap - EXIT
			info "KEEP_SIM_DIR=1 — directory kept"
		fi
		;;
	all)
		require_repo_root
		cyt_run_all
		info "all done"
		;;
	ci)
		require_repo_root
		cyt_section "CI"
		cyt_sync_app
		cyt_verify_app_python
		cyt_verify_sdk_import
		if command -v ast-grep >/dev/null 2>&1; then
			info "ast-grep scan"
			cyt_run ast-grep scan src/ sdk/
		else
			info "skip ast-grep (not on PATH)"
		fi
		cyt_run uv run python scripts/check_agent_imports.py
		cyt_run uv run python scripts/check_cyt_client_imports.py
		if command -v ruff >/dev/null 2>&1 || [[ -x "${CYT_VENV_BIN}/ruff" ]]; then
			info "ruff check"
			cyt_run uv run ruff check src/cyt src/tests
		else
			info "skip ruff (not on PATH)"
		fi
		cyt_test_app_python
		cyt_build_app_wheel
		;;
	"" | -h | --help | help)
		usage
		;;
	*)
		if [[ -n "${CYT_LOCAL_DEV_SHORT:-}" ]]; then
			die "unknown command: ${cmd}"
		fi
		echo "unknown command: ${cmd}" >&2
		echo >&2
		usage >&2
		return 1
		;;
	esac
}

if [[ -n "${CYT_LOCAL_DEV_SHORT}" ]]; then
	_cyt_local_dev_main "${LOCAL_DEV_ARGS[@]}" 2>&1 | "${SCRIPT_DIR}/shorten-paths.sh" | cyt_filter_short_logs
else
	_cyt_local_dev_main "${LOCAL_DEV_ARGS[@]}" 2>&1 | "${SCRIPT_DIR}/shorten-paths.sh"
fi
exit "${PIPESTATUS[0]}"
