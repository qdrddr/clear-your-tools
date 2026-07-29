#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers for local monorepo development (source scripts/local-dev-lib.sh).
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
		[[ -n "${CYT_LOCAL_DEV_SHORT:-}" ]] && return 0
		echo "==> $*"
	}

	cyt_section() {
		[[ -n "${CYT_LOCAL_DEV_SHORT:-}" ]] && return 0
		echo ""
		echo "$*"
	}

	# Run a command; suppress stdout in short/silent mode (stderr still visible).
	cyt_run() {
		if [[ -n "${CYT_LOCAL_DEV_SHORT:-}" ]]; then
			"$@" >/dev/null
		else
			"$@"
		fi
	}

	# Keep only error/warning lines when CYT_LOCAL_DEV_SHORT is set (pipe after shorten_paths).
	cyt_filter_short_logs() {
		awk '
			BEGIN {
				IGNORECASE = 1
				ld_grp_count = 0
				ld_grp_key = ""
				ld_grp_header = ""
				ld_grp_max_items = 8
			}

			function ld_grp_flush(    i, shown, more) {
				if (ld_grp_count == 0) return
				print ld_grp_header (ld_grp_count > 1 ? " [" ld_grp_count " members]" : "")
				shown = ld_grp_count
				if (shown > ld_grp_max_items) shown = ld_grp_max_items
				for (i = 1; i <= shown; i++) print ld_grp_items[i]
				more = ld_grp_count - shown
				if (more > 0) print "... +" more " more members"
				ld_grp_count = 0
				ld_grp_key = ""
				ld_grp_header = ""
				delete ld_grp_items
			}

			# macOS/iOS ld: group archive member version skew warnings.
			# Input:  ld: warning: object file (lib.a[336](obj.o)) was built for newer '"'"'macOS'"'"' version (26.5) than being linked (26.0)
			# Output: ld: warning: object file (lib.a was built for newer '"'"'macOS'"'"' version (26.5) than being linked (26.0) [N members]
			#         [336](obj.o))
			function ld_try_group_object_warning(line,    s, p1, p2, p3, rest, key, header, item, marker) {
				if (line !~ /^ld:[[:space:]]+warning:[[:space:]]+object file \(/)
					return 0
				s = line
				sub(/^ld:[[:space:]]+warning:[[:space:]]+object file \(/, "", s)
				p1 = index(s, "[")
				if (p1 == 0) return 0
				ld_archive = substr(s, 1, p1 - 1)
				rest = substr(s, p1 + 1)
				p2 = index(rest, "](")
				if (p2 == 0) return 0
				ld_idx = substr(rest, 1, p2 - 1)
				rest = substr(rest, p2 + 2)
				marker = ")) was built for newer "
				p3 = index(rest, marker)
				if (p3 == 0) return 0
				ld_obj = substr(rest, 1, p3 - 1)
				rest = substr(rest, p3 + length(marker))
				if (rest !~ /^'"'"'[^'"'"']+'"'"' version \([^)]+\) than being linked \([^)]+\)$/)
					return 0
				ld_os = rest
				sub(/^'"'"'/, "", ld_os)
				sub(/'"'"' version \(.*/, "", ld_os)
				ld_build = rest
				sub(/^'"'"'[^'"'"']+'"'"' version \(/, "", ld_build)
				sub(/\) than being linked \(.*/, "", ld_build)
				ld_link = rest
				sub(/^[^)]+\) than being linked \(/, "", ld_link)
				sub(/\)$/, "", ld_link)

				key = ld_archive SUBSEP ld_os SUBSEP ld_build SUBSEP ld_link
				header = "ld: warning: object file (" ld_archive " was built for newer \047" ld_os "\047 version (" ld_build ") than being linked (" ld_link ")"
				item = "[" ld_idx "](" ld_obj "))"
				if (key != ld_grp_key) ld_grp_flush()
				ld_grp_key = key
				ld_grp_header = header
				ld_grp_count++
				ld_grp_items[ld_grp_count] = item
				return 1
			}

			{
				if (ld_try_group_object_warning($0)) next

				ld_grp_flush()

				if ($0 ~ /^==>/) next
				if ($0 ~ /^OK:/) next
				if ($0 ~ /^  /) next
				if ($0 ~ /^[━=─#]{3,}/) next
				if ($0 ~ /^=+ test session starts/) next
				if ($0 ~ /^=+ FAILURES =+/) { print; next }
				if ($0 ~ /^=+ short test summary/) { print; next }
				if ($0 ~ /^platform /) next
				if ($0 ~ /^collected /) next
				if ($0 ~ /^test result:/) next
				if ($0 ~ /^[[:space:]]*Compiling /) next
				if ($0 ~ /^[[:space:]]*Finished /) next
				if ($0 ~ /^[[:space:]]*Running /) next
				if ($0 ~ /^   Doc-tests /) next
				if ($0 ~ /^running [0-9]+ test/) next
				if ($0 ~ /^test result: ok/) next
				if ($0 ~ /^test .* \.\.\. ok/) next
				if ($0 ~ /^passed, 0 failed/) next
				if ($0 ~ /^error:/) { print; next }
				if ($0 ~ / error:/) { print; next }
				if ($0 ~ /^warning:/) { print; next }
				if ($0 ~ / warning:/) { print; next }
				if ($0 ~ /fatal error/) { print; next }
				if ($0 ~ /undefined symbols/) { print; next }
				if ($0 ~ /^ld: warning: object file \(.*was built for newer /) next
				if ($0 ~ /^ld: /) { print; next }
				if ($0 ~ /^clang: error/) { print; next }
				if ($0 ~ /: error:/) { print; next }
				if ($0 ~ /^\*\*\* /) { print; next }
				if ($0 ~ /npm warn/) { print; next }
				if ($0 ~ /panic!/) { print; next }
				if ($0 ~ /thread .* panicked/) { print; next }
				if ($0 ~ /AssertionError/) { print; next }
				if ($0 ~ /not ok /) { print; next }
				if ($0 ~ /^E[[:space:]]+/) { print; next }
				if ($0 ~ /FAILED/) { print; next }
				if ($0 ~ /failed/ && $0 !~ /0 failed/ && $0 !~ /passed, 0 failed/) { print; next }
				if ($0 ~ /failure/ && $0 !~ /failure info/) { print; next }
				if ($0 ~ /✖/) { print; next }
				if ($0 ~ /sys\.exit/) { print; next }
				if ($0 ~ /unknown command:/) { print; next }
			}

			END { ld_grp_flush() }
		'
	}

	require_cmd() {
		command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
	}

	cyt_cmake_make_program() {
		local candidate
		for candidate in gmake make; do
			if command -v "$candidate" >/dev/null 2>&1; then
				command -v "$candidate"
				return 0
			fi
		done
		die "missing required command: make or gmake"
	}

	cyt_npm() {
		env -u npm_config_devdir npm "$@"
	}

	require_repo_root() {
		[[ -f "${CYT_REPO_ROOT}/pyproject.toml" ]] || die "not a repo root: ${CYT_REPO_ROOT}"
		[[ -f "${CYT_REPO_ROOT}/sdk/python/pyproject.toml" ]] || die "missing sdk/python"
		[[ -f "${CYT_REPO_ROOT}/sdk/rust/cyt-indexer/Cargo.toml" ]] || die "missing sdk/rust/cyt-indexer"
	}

	# Install/sync the main app workspace venv (src/ + editable sdk/python via pyproject.toml).
	cyt_sync_app() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "uv sync"
		cyt_run uv sync --all-extras --group dev --group test --locked
	}

	# Backward-compatible alias.
	cyt_sync_workspace() {
		cyt_sync_app
	}

	cyt_sync_sdk_python() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}/sdk/python" || die "cd failed"
		info "uv sync sdk/python"
		cyt_run uv sync
	}

	cyt_indexer_release() {
		require_cmd cargo
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "cargo build -p cyt-indexer --release"
		cyt_run env -u CARGO_TARGET_DIR cargo build -p cyt-indexer --release
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
		info "cyt-indexer build skills"
		cyt_run "${CYT_INDEXER_BIN}" build skills "${forwarded[@]}"

		[[ -d "${output_dir}/skills/decomposed" ]] ||
			die "skills build did not produce ${output_dir}/skills/decomposed"
		local doc_count
		doc_count="$(
			find "${output_dir}/skills/decomposed" -mindepth 2 -maxdepth 2 -name page_index.json 2>/dev/null |
				wc -l | tr -d ' '
		)"
		[[ "${doc_count}" -gt 0 ]] ||
			die "no skill page_index.json files in ${output_dir}/skills/decomposed"
		info "skills build ok (${doc_count} docs)"
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

		info "extract tools from example json"
		cyt_run jq '.body.tools' "${example}" >"${tools_json}"

		mkdir -p "${CYT_CATALOG_DIR}"
		info "cyt-indexer build tools"
		cyt_run "${CYT_INDEXER_BIN}" build tools --tools "${tools_json}" --output "${CYT_CATALOG_DIR}"
		rm -f "${tools_json}"

		[[ -f "${CYT_CATALOG_DIR}/tools.json" ]] || die "catalog build did not produce ${CYT_CATALOG_DIR}/tools.json"
		local decomposed_count
		decomposed_count="$(find "${CYT_CATALOG_DIR}/schemas/decomposed" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')"
		[[ "${decomposed_count}" -gt 1 ]] || die "expected multiple decomposed json files, got ${decomposed_count}"
		info "catalog build ok (${decomposed_count} files)"
	}

	cyt_indexer_extract_survivors() {
		require_cmd jq
		cyt_indexer_paths

		local example="${CYT_EXAMPLE_JSON}"
		[[ -f "${example}" ]] || die "missing ${example}"
		mkdir -p "${CYT_CATALOG_DIR}"

		info "extract rerank survivors"
		cyt_run jq '{
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

		info "cyt-indexer retrieve skills"
		cyt_run "${CYT_INDEXER_BIN}" retrieve skills "$@"

		local catalog_dir=""
		local output_file=""
		while [[ $# -gt 0 ]]; do
			case "$1" in
			--catalog)
				[[ $# -ge 2 ]] || die "missing value for --catalog"
				catalog_dir="$2"
				shift 2
				;;
			--catalog=*)
				catalog_dir="${1#*=}"
				shift
				;;
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
		if [[ -n "${catalog_dir}" && "${output_file}" != /* ]]; then
			output_file="${catalog_dir%/}/${output_file}"
		fi
		[[ -s "${output_file}" ]] || die "skills retrieve produced empty ${output_file}"
		info "skills retrieve ok"
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

		info "cyt-indexer retrieve tools"
		cyt_run "${CYT_INDEXER_BIN}" retrieve tools \
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
		info "retrieve ok (${tool_count} tools)"
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
		info "cargo test -p cyt-indexer --features testing,ffi"
		cyt_run env -u CARGO_TARGET_DIR cargo test -p cyt-indexer --features testing,ffi
		cyt_test_indexer_build
	}

	cyt_build_sdk_python() {
		require_cmd uv
		cyt_sync_sdk_python
		cd "${CYT_REPO_ROOT}/sdk/python" || die "cd failed"
		info "maturin develop --release"
		cyt_run uv run maturin develop --release
		info "pytest sdk/python/tests/unit"
		cyt_run env SKIP_MATURIN_DEVELOP=1 bash "${CYT_REPO_ROOT}/scripts/pytest-sdk-python.sh"
	}

	cyt_build_sdk_typescript() {
		require_cmd npm
		cd "${CYT_REPO_ROOT}/sdk/typescript" || die "cd failed"
		info "npm ci, build, test"
		# Avoid ENOTEMPTY when a prior native build left locked @napi-rs artifacts.
		rm -rf node_modules
		cyt_run cyt_npm ci
		cyt_run cyt_npm run build
		cyt_run cyt_npm test
	}

	cyt_build_sdk_c() {
		require_cmd cmake
		require_cmd ctest
		require_cmd rustc
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		local triplet make_prog
		triplet="$(rustc -vV | sed -n 's/^host: //p')"
		make_prog="$(cyt_cmake_make_program)"
		info "build C FFI (sdk/c, ${triplet})"
		cyt_run env -u CARGO_TARGET_DIR bash sdk/c/scripts/build-c-lib.sh --target "${triplet}"
		info "cmake configure + build"
		cyt_run env -u CARGO_TARGET_DIR cmake -S sdk/c -B sdk/c/build \
			-DCMAKE_BUILD_TYPE=Release \
			-DCYT_RUST_TARGET="${triplet}" \
			-DCMAKE_MAKE_PROGRAM="${make_prog}"
		cyt_run env -u CARGO_TARGET_DIR cmake --build sdk/c/build
		info "ctest sdk/c"
		cyt_run env -u CARGO_TARGET_DIR ctest --test-dir sdk/c/build --output-on-failure
	}

	cyt_build_sdk_go() {
		require_cmd go
		require_cmd rustc
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "build C FFI (sdk/go)"
		cyt_run env -u CARGO_TARGET_DIR bash sdk/c/scripts/build-c-lib.sh --no-sync-header
		cd "${CYT_REPO_ROOT}/sdk/go" || die "cd failed"
		export CGO_ENABLED=1
		local host_triplet
		host_triplet="$(rustc -vV | sed -n 's/^host: //p')"
		export PATH="${CYT_REPO_ROOT}/target/${host_triplet}/release:${PATH}"
		info "go native ensure"
		cyt_run go run ./cmd/cyt-native-ensure -static-only
		info "go test ./..."
		cyt_run env -u CARGO_TARGET_DIR go test ./...
	}

	cyt_build_all_sdks() {
		cyt_build_sdk_python
		cyt_build_sdk_c
		cyt_build_sdk_go
		cyt_build_sdk_typescript
	}

	# Fail if cyt-indexer-sdk is not the checkout under sdk/python (e.g. PyPI-only install).
	cyt_verify_sdk_python() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}/sdk/python" || die "cd failed"
		info "verify sdk/python"
		cyt_run uv run python - "${CYT_REPO_ROOT}" <<'PY'
import json
import sys
from importlib import metadata
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sdk_root = (root / "sdk" / "python").resolve()

try:
    dist = metadata.distribution("cyt-indexer-sdk")
except metadata.PackageNotFoundError:
    sys.exit("cyt-indexer-sdk is not installed; run: ./scripts/local-dev.sh sdk-python")

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
            "Use this repo's pyproject.toml [tool.uv.sources] and run ./scripts/local-dev.sh app-setup"
        )
    install_kind = "path"

from cyt_indexer._native import build_catalog_index

if not callable(build_catalog_index):
    sys.exit("cyt_indexer._native.build_catalog_index is not callable (rebuild with sdk-python)")

print("OK: local cyt-indexer-sdk (sdk/python)")
print(f"  sdk root: {sdk_root}")
print(f"  install: {install_kind}")
PY
	}

	cyt_verify_app_python() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "verify app"
		cyt_run uv run python - <<'PY'
from cyt_indexer.build import build_catalog_index as sdk_build
from cyt.indexer.build import build_catalog_index as app_build

if sdk_build is not app_build:
    sys.exit("cyt.indexer.build does not re-export cyt_indexer.build.build_catalog_index")

print("OK: main app (src/) uses local cyt-indexer-sdk")
PY
	}

	# Backward-compatible alias (sdk + app integration checks).
	cyt_verify_local_sdk() {
		cyt_verify_sdk_python
		cyt_verify_app_python
	}

	cyt_verify_sdk_import() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		cyt_run uv run python -c "from cyt_indexer._native import build_catalog_index; assert callable(build_catalog_index)"
	}

	cyt_test_app_python() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "pytest app categories (unit, gherkin-unit, quality_metrics, coverage, mutation)"
		cyt_run bash "${CYT_REPO_ROOT}/scripts/pytest-app-ci.sh"
	}

	cyt_test_app() {
		cyt_verify_app_python
		cyt_test_app_python
	}

	cyt_build_app_wheel() {
		require_cmd uv
		cd "${CYT_REPO_ROOT}" || die "cd failed"
		info "uv build"
		# Keep uv's cache outside the repo so local .uv-cache/ never lands in sdists.
		UV_CACHE_DIR="${UV_CACHE_DIR:-${TMPDIR:-/tmp}/cyt-uv-cache}"
		export UV_CACHE_DIR
		cyt_run uv build
	}

	cyt_build_all_app() {
		cyt_sync_app
		cyt_verify_app_python
		cyt_test_app_python
		cyt_build_app_wheel
	}

	cyt_run_all() {
		cyt_section "Core (Rust)"
		cyt_build_rust

		cyt_section "SDK: Python"
		cyt_build_sdk_python
		cyt_verify_sdk_python

		# C/Go before TypeScript: napi build uses the same dylib name and would
		# overwrite the C FFI shared library if TypeScript ran first.
		cyt_section "SDK: C"
		cyt_build_sdk_c

		cyt_section "SDK: Go"
		cyt_build_sdk_go

		cyt_section "SDK: TypeScript"
		cyt_build_sdk_typescript

		cyt_section "Main app"
		cyt_build_all_app
	}

	# Expected .env locations (same order as src/cyt/config load_proxy_env):
	#   1. ${CYT_REPO_ROOT}/.env          e.g. .../tool-attention/.env
	#   2. ${HOME}/.config/cyt/.env
	# If a key is still unset, fall back to macOS Keychain (scripts/proxy.sh).
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
		security find-generic-password -s "cyt" -a "${var_name}" -w 2>/dev/null
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
			info "loaded ${var_name} from macOS Keychain (service: cyt)"
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
		cyt_verify_app_python
		cyt_ensure_proxy_api_keys
		info "proxy"
		exec uv run src/cyt/proxy/cli.py proxy "$@"
	}

fi
