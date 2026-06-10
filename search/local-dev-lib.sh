#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers for local monorepo development (source search/local-dev-lib.sh).
# Not meant to be executed directly.

if [[ -z "${CYT_LOCAL_DEV_LIB_SOURCED:-}" ]]; then
	CYT_LOCAL_DEV_LIB_SOURCED=1

	CYT_REPO_ROOT="${CYT_REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
	export CYT_REPO_ROOT

	CYT_VENV_BIN="${CYT_REPO_ROOT}/.venv/bin"
	export PATH="${CYT_VENV_BIN}:${PATH}"

	die() {
		echo "error: $*" >&2
		exit 1
	}

	info() {
		echo "==> $*"
	}

	require_cmd() {
		command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
	}

	require_repo_root() {
		[[ -f "${CYT_REPO_ROOT}/pyproject.toml" ]] || die "not a repo root: ${CYT_REPO_ROOT}"
		[[ -f "${CYT_REPO_ROOT}/sdk/python/pyproject.toml" ]] || die "missing sdk/python"
		[[ -f "${CYT_REPO_ROOT}/sdk/rust/cyt-indexer/Cargo.toml" ]] || die "missing sdk/rust/cyt-indexer"
	}

	# Install/sync the workspace venv with [tool.uv.sources] path + editable SDK.
	cyt_sync_workspace() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "uv sync (editable cyt-indexer-sdk from sdk/python via pyproject.toml [tool.uv.sources])"
		uv sync --all-extras --group dev --group test --locked
	}

	cyt_indexer_release() {
		require_cmd cargo
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "cargo build -p cyt-indexer --release"
		# Use workspace target/; ignore sandbox CARGO_TARGET_DIR if set by the IDE.
		env -u CARGO_TARGET_DIR cargo build -p cyt-indexer --release
	}

	cyt_indexer_paths() {
		CYT_INDEXER_BIN="${CYT_REPO_ROOT}/target/release/cyt-indexer"
		CYT_CATALOG_DIR="${CYT_CATALOG_DIR:-${CYT_REPO_ROOT}/.catalog}"
		CYT_EXAMPLE_JSON="${CYT_EXAMPLE_JSON:-${CYT_REPO_ROOT}/debug/full_example.json}"
		CYT_SURVIVORS_JSON="${CYT_SURVIVORS_JSON:-${CYT_CATALOG_DIR}/survivors.json}"
		CYT_RETRIEVE_OUT="${CYT_RETRIEVE_OUT:-${CYT_CATALOG_DIR}/out.json}"
	}

	cyt_indexer_build_skills() {
		cyt_indexer_paths
		cyt_indexer_release
		[[ -x "${CYT_INDEXER_BIN}" ]] || die "cyt-indexer binary not found at ${CYT_INDEXER_BIN}"

		[[ $# -gt 0 ]] || die "indexer build skills requires --skills DIR [--skills DIR...] --output DIR"

		local output_dir="${CYT_CATALOG_DIR}"
		local -a forwarded=()
		while [[ $# -gt 0 ]]; do
			case "$1" in
			--output)
				[[ $# -ge 2 ]] || die "missing value for --output"
				output_dir="$2"
				forwarded+=(--output "$2")
				shift 2
				;;
			--output=*)
				output_dir="${1#*=}"
				forwarded+=("$1")
				shift
				;;
			*)
				forwarded+=("$1")
				shift
				;;
			esac
		done

		mkdir -p "${output_dir}"
		info "cyt-indexer build skills ${forwarded[*]}"
		"${CYT_INDEXER_BIN}" build skills "${forwarded[@]}"

		[[ -f "${output_dir}/skills_index.json" ]] ||
			die "skills build did not produce ${output_dir}/skills_index.json"
		[[ -d "${output_dir}/skills/decomposed" ]] ||
			die "skills build did not produce ${output_dir}/skills/decomposed"
		local doc_count
		doc_count="$(
			find "${output_dir}/skills/decomposed" -mindepth 1 -maxdepth 1 -type d 2>/dev/null |
				wc -l | tr -d ' '
		)"
		[[ "${doc_count}" -gt 0 ]] ||
			die "no skill documents in ${output_dir}/skills/decomposed"
		info "skills build ok (${doc_count} documents -> ${output_dir}/skills/decomposed)"
	}

	cyt_indexer_build_catalog() {
		require_cmd jq
		cyt_indexer_paths

		local example="${CYT_EXAMPLE_JSON}"
		[[ -f "${example}" ]] || die "missing ${example}"

		cyt_indexer_release
		[[ -x "${CYT_INDEXER_BIN}" ]] || die "cyt-indexer binary not found at ${CYT_INDEXER_BIN}"

		local tools_json
		tools_json="$(mktemp "${TMPDIR:-/tmp}/cyt-tools.XXXXXX")"

		info "extract tools from ${example}"
		jq '.body.tools' "${example}" >"${tools_json}"

		mkdir -p "${CYT_CATALOG_DIR}"
		info "cyt-indexer build tools --tools ${tools_json} --output ${CYT_CATALOG_DIR}"
		"${CYT_INDEXER_BIN}" build tools --tools "${tools_json}" --output "${CYT_CATALOG_DIR}"
		rm -f "${tools_json}"

		[[ -f "${CYT_CATALOG_DIR}/tools.json" ]] || die "catalog build did not produce ${CYT_CATALOG_DIR}/tools.json"
		local decomposed_count
		decomposed_count="$(find "${CYT_CATALOG_DIR}/schemas/decomposed" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
		[[ "${decomposed_count}" -gt 1 ]] || die "expected multiple decomposed json files, got ${decomposed_count}"
		info "catalog build ok (${decomposed_count} decomposed json files)"
	}

	cyt_indexer_extract_survivors() {
		require_cmd jq
		cyt_indexer_paths

		local example="${CYT_EXAMPLE_JSON}"
		[[ -f "${example}" ]] || die "missing ${example}"
		mkdir -p "${CYT_CATALOG_DIR}"

		info "extract rerank survivors from ${example} -> ${CYT_SURVIVORS_JSON}"
		jq '{
		  json: [.pruning.decomposed_catalog.rerank.json[]? | .score |= (tonumber)],
		  md:   [.pruning.decomposed_catalog.rerank.md[]?   | .score |= (tonumber)]
		}' "${example}" >"${CYT_SURVIVORS_JSON}"

		local json_count md_count
		json_count="$(jq '.json | length' "${CYT_SURVIVORS_JSON}")"
		md_count="$(jq '.md | length' "${CYT_SURVIVORS_JSON}")"
		[[ "${json_count}" -gt 0 || "${md_count}" -gt 0 ]] ||
			die "no rerank survivors in ${example} (.pruning.decomposed_catalog.rerank)"
		info "survivors ok (json=${json_count}, md=${md_count})"
	}

	cyt_indexer_retrieve_skills() {
		cyt_indexer_paths
		[[ -x "${CYT_INDEXER_BIN}" ]] || cyt_indexer_release
		[[ -x "${CYT_INDEXER_BIN}" ]] || die "cyt-indexer binary not found at ${CYT_INDEXER_BIN}"

		[[ $# -gt 0 ]] ||
			die "indexer retrieve skills requires --catalog DIR --doc-id ID --query metadata|structure|content --output FILE"

		info "cyt-indexer retrieve skills $*"
		"${CYT_INDEXER_BIN}" retrieve skills "$@"

		local output_file=""
		while [[ $# -gt 0 ]]; do
			case "$1" in
			--output)
				[[ $# -ge 2 ]] || die "missing value for --output"
				output_file="$2"
				shift 2
				;;
			--output=*)
				output_file="${1#*=}"
				shift
				;;
			*)
				shift
				;;
			esac
		done
		[[ -n "${output_file}" ]] || die "missing --output for skills retrieve"
		[[ -s "${output_file}" ]] || die "skills retrieve produced empty ${output_file}"
		info "skills retrieve ok -> ${output_file}"
	}

	cyt_indexer_retrieve() {
		cyt_indexer_paths
		[[ -d "${CYT_CATALOG_DIR}" ]] || die "missing catalog dir ${CYT_CATALOG_DIR}; run indexer build first"
		[[ -f "${CYT_SURVIVORS_JSON}" ]] || cyt_indexer_extract_survivors
		[[ -x "${CYT_INDEXER_BIN}" ]] || cyt_indexer_release
		[[ -x "${CYT_INDEXER_BIN}" ]] || die "cyt-indexer binary not found at ${CYT_INDEXER_BIN}"

		local system_policy="${CYT_INDEXER_SYSTEM_POLICY:-prune_optional}"
		local mcp_policy="${CYT_INDEXER_MCP_POLICY:-prune_all}"
		local tool_policies=()
		local default_tool_policies="AskUserQuestion=always_include"
		local policy_source="${CYT_INDEXER_TOOL_POLICIES-${default_tool_policies}}"
		if [[ -n "${policy_source}" ]]; then
			local spec
			for spec in ${policy_source}; do
				tool_policies+=(--tool-policy "${spec}")
			done
		fi

		while [[ $# -gt 0 ]]; do
			case "$1" in
			--tool-policy)
				[[ $# -ge 2 ]] || die "missing value for --tool-policy"
				tool_policies+=(--tool-policy "$2")
				shift 2
				;;
			--tool-policy=*)
				tool_policies+=("$1")
				shift
				;;
			--system-policy)
				[[ $# -ge 2 ]] || die "missing value for --system-policy"
				system_policy="$2"
				shift 2
				;;
			--system-policy=*)
				system_policy="${1#*=}"
				shift
				;;
			--mcp-policy)
				[[ $# -ge 2 ]] || die "missing value for --mcp-policy"
				mcp_policy="$2"
				shift 2
				;;
			--mcp-policy=*)
				mcp_policy="${1#*=}"
				shift
				;;
			--output)
				[[ $# -ge 2 ]] || die "missing value for --output"
				CYT_RETRIEVE_OUT="$2"
				shift 2
				;;
			--output=*)
				CYT_RETRIEVE_OUT="${1#*=}"
				shift
				;;
			--per-tool | --per-tool=* | --config | --config=*)
				tool_policies+=("$1")
				if [[ "$1" != *=* ]]; then
					[[ $# -ge 2 ]] || die "missing value for $1"
					tool_policies+=("$2")
					shift
				fi
				shift
				;;
			*)
				die "unknown indexer retrieve arg: $1"
				;;
			esac
		done

		info "cyt-indexer retrieve tools -> ${CYT_RETRIEVE_OUT}"
		"${CYT_INDEXER_BIN}" retrieve tools \
			--catalog "${CYT_CATALOG_DIR}" \
			--input "${CYT_SURVIVORS_JSON}" \
			--output "${CYT_RETRIEVE_OUT}" \
			--system-policy "${system_policy}" \
			--mcp-policy "${mcp_policy}" \
			"${tool_policies[@]}"

		[[ -s "${CYT_RETRIEVE_OUT}" ]] || die "retrieve produced empty ${CYT_RETRIEVE_OUT}"
		require_cmd jq
		local tool_count
		tool_count="$(jq 'length' "${CYT_RETRIEVE_OUT}")"
		[[ "${tool_count}" -gt 0 ]] || die "retrieve produced no tools in ${CYT_RETRIEVE_OUT}"
		info "retrieve ok (${tool_count} tools -> ${CYT_RETRIEVE_OUT})"
	}

	cyt_indexer_all() {
		cyt_indexer_build_catalog
		cyt_indexer_extract_survivors
		cyt_indexer_retrieve "$@"
	}

	cyt_test_indexer_build() {
		cyt_indexer_build_catalog
	}

	cyt_build_rust() {
		require_cmd cargo
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "cargo test -p cyt-indexer"
		cargo test -p cyt-indexer
		cyt_test_indexer_build
	}

	cyt_build_sdk_python() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}/sdk/python" || die "cd failed"
		uv sync
		info "maturin develop --release (native extension from sdk/rust/cyt-indexer)"
		uv run maturin develop --release
	}

	cyt_build_sdk_typescript() {
		require_cmd npm
		cd "${CYT_REPO_ROOT}/sdk/typescript" || die "cd failed"
		info "npm ci && npm run build && npm test"
		npm ci
		npm run build
		npm test
	}

	# Fail if cyt-indexer-sdk is not the checkout under sdk/python (e.g. PyPI-only install).
	cyt_verify_local_sdk() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "verify cyt-indexer-sdk resolves to local sdk/python (not a registry-only install)"
		uv run python - "${CYT_REPO_ROOT}" <<'PY'
import json
import sys
from importlib import metadata
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sdk_root = (root / "sdk" / "python").resolve()

try:
    dist = metadata.distribution("cyt-indexer-sdk")
except metadata.PackageNotFoundError:
    sys.exit("cyt-indexer-sdk is not installed; run: ./search/local-dev.sh setup sdk-python")

install_kind = "editable"
try:
    direct = json.loads(dist.read_text("direct_url.json"))
    url = str(direct.get("url", "")).replace("\\", "/")
    if "sdk/python" not in url:
        sys.exit(
            "cyt-indexer-sdk direct_url.json does not point at sdk/python:\n" + url
        )
except FileNotFoundError:
    import cyt_indexer

    pkg_dir = Path(cyt_indexer.__file__).resolve()
    if sdk_root not in pkg_dir.parents:
        sys.exit(
            "cyt-indexer-sdk is not loaded from sdk/python\n"
            f"  package file: {pkg_dir}\n"
            f"  expected under: {sdk_root}\n"
            "Use this repo's pyproject.toml [tool.uv.sources] and run ./search/local-dev.sh setup"
        )
    install_kind = "path"

from cyt_indexer._native import build_catalog_index

if not callable(build_catalog_index):
    sys.exit("cyt_indexer._native.build_catalog_index is not callable (rebuild with sdk-python)")

from cyt_indexer.build import build_catalog_index as sdk_build
from cyt.indexer.build import build_catalog_index as app_build

if sdk_build is not app_build:
    sys.exit("cyt.indexer.build does not re-export cyt_indexer.build.build_catalog_index")

print("OK: local cyt-indexer-sdk (not a registry-only install)")
print(f"  sdk root: {sdk_root}")
print(f"  install: {install_kind}")
PY
	}

	cyt_verify_sdk_import() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		uv run python -c "from cyt_indexer._native import build_catalog_index; assert callable(build_catalog_index)"
	}

	cyt_test_app() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		cyt_verify_local_sdk
		info "pytest src/tests"
		uv run pytest src/tests
	}

	cyt_build_app_wheel() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "uv build (clear-your-tools sdist/wheel; depends on pinned cyt-indexer-sdk version in metadata)"
		uv build
	}

	# Expected .env locations (same order as src/cyt/config load_proxy_env):
	#   1. ${CYT_REPO_ROOT}/.env          e.g. .../tool-attention/.env
	#   2. ${HOME}/.config/cyt/.env
	# If a key is still unset, fall back to macOS Keychain (search/proxy.sh).
	CYT_ENV_PATHS=(
		"${CYT_REPO_ROOT}/.env"
		"${HOME}/.config/cyt/.env"
	)

	_cyt_read_dotenv_var() {
		local env_file="$1" var_name="$2"
		[[ -f "${env_file}" ]] || return 1

		local line value
		while IFS= read -r line || [[ -n "${line}" ]]; do
			[[ "${line}" =~ ^[[:space:]]*# ]] && continue
			[[ "${line}" =~ ^[[:space:]]*$ ]] && continue
			if [[ "${line}" =~ ^[[:space:]]*${var_name}=(.*)$ ]]; then
				value="${BASH_REMATCH[1]}"
				value="${value#"${value%%[![:space:]]*}"}"
				value="${value%"${value##*[![:space:]]}"}"
				if [[ "${value}" =~ ^\"(.*)\"$ ]]; then
					value="${BASH_REMATCH[1]}"
				elif [[ "${value}" =~ ^\'(.*)\'$ ]]; then
					value="${BASH_REMATCH[1]}"
				fi
				[[ -n "${value}" ]] || return 1
				printf '%s' "${value}"
				return 0
			fi
		done <"${env_file}"
		return 1
	}

	_cyt_keychain_api_key() {
		local var_name="$1"
		command -v security >/dev/null 2>&1 || return 1
		security find-generic-password -s "nono" -a "${var_name}" -w 2>/dev/null
	}

	_cyt_ensure_api_key() {
		local var_name="$1"
		local current="${!var_name:-}"
		if [[ -n "${current}" ]]; then
			return 0
		fi

		local env_file value
		for env_file in "${CYT_ENV_PATHS[@]}"; do
			if value="$(_cyt_read_dotenv_var "${env_file}" "${var_name}")"; then
				export "${var_name}=${value}"
				info "loaded ${var_name} from ${env_file}"
				return 0
			fi
		done

		if value="$(_cyt_keychain_api_key "${var_name}")"; then
			export "${var_name}=${value}"
			info "loaded ${var_name} from macOS Keychain (service: nono)"
			return 0
		fi

		return 1
	}

	cyt_ensure_proxy_api_keys() {
		_cyt_ensure_api_key OPENROUTER_API_KEY || true
		_cyt_ensure_api_key DEEPINFRA_API_KEY || true
	}

	cyt_run_proxy() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		cyt_verify_local_sdk
		cyt_ensure_proxy_api_keys
		info "proxy via checkout CLI (src/cyt), local SDK"
		exec uv run src/cyt/proxy/cli.py proxy "$@"
	}

fi
