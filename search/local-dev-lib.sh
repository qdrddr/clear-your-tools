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

	cyt_test_indexer_build() {
		require_cmd cargo
		require_cmd jq
		cd "${CYT_REPO_ROOT}" || die "cd failed"

		local example="${CYT_REPO_ROOT}/debug/full_example.json"
		[[ -f "${example}" ]] || die "missing ${example}"

		info "cargo build -p cyt-indexer --release"
		cargo build -p cyt-indexer --release

		local tools_json catalog indexer
		tools_json="$(mktemp "${TMPDIR:-/tmp}/cyt-tools.XXXXXX.json")"
		catalog="${CYT_REPO_ROOT}/.catalog"
		indexer="${CYT_REPO_ROOT}/target/release/cyt-indexer"

		info "extract tools from debug/full_example.json"
		jq '.body.tools' "${example}" >"${tools_json}"

		[[ -x "${indexer}" ]] || die "cyt-indexer binary not found at ${indexer}"
		info "cyt-indexer build --tools ${tools_json} --output ${catalog}"
		"${indexer}" build --tools "${tools_json}" --output "${catalog}"
		rm -f "${tools_json}"

		[[ -f "${catalog}/tools.json" ]] || die "catalog build did not produce ${catalog}/tools.json"
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
