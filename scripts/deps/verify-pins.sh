#!/usr/bin/env bash
# Verify dependency pins and lockfile freshness across monorepo ecosystems.
#
# Usage:
#   ./scripts/deps/verify-pins.sh
#   ./scripts/deps/verify-pins.sh --short
#   ./scripts/deps/verify-pins.sh --skip rust --skip npm
#   ./scripts/deps/verify-pins.sh --output-dir /tmp/pin-audit
#
# Writes reports under scripts/deps/output/audit-YYYYMMDD-HHMMSS/ by default.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CYT_INDEXER_CARGO="${REPO_ROOT}/sdk/rust/cyt-indexer/Cargo.toml"

# shellcheck source=scripts/lib/shorten-paths.sh
source "${SCRIPT_DIR}/../lib/shorten-paths.sh"
export SHORTEN_ROOT="${REPO_ROOT}"

DO_MANIFEST_LINT=1
DO_REPORT=1
OUTPUT_DIR=""
SHORT=false
SKIP_PYTHON=0
SKIP_RUST=0
SKIP_NPM=0
SKIP_GO=0
SKIP_C=0

DEPS_OUTPUT_DIR=""
DEPS_SUMMARY_FILE=""
MANIFEST_LINT_FILE=""
FAILURES=0

emit_out() {
	shorten_paths
}

emit_err() {
	shorten_paths >&2
}

filter_errors_warnings() {
	awk 'BEGIN { IGNORECASE = 1 }
		/error|warning|warn:|fail|mismatch|out of sync|missing |loose / { print }'
}

emit_check_output() {
	local file="$1"
	[[ -s "${file}" ]] || return 0
	if [[ "${SHORT}" == true ]]; then
		if grep -E -i 'error|warning|warn:|fail|mismatch|out of sync|missing|loose' "${file}" >/dev/null 2>&1; then
			grep -E -i 'error|warning|warn:|fail|mismatch|out of sync|missing|loose' "${file}" |
				filter_errors_warnings |
				emit_err
		else
			tail -n 15 "${file}" | emit_err
		fi
	else
		cat "${file}" | emit_err
	fi
}

die() {
	printf 'error: %s\n' "$*" | emit_err
	exit 1
}

info() {
	[[ "${SHORT}" == true ]] && return 0
	printf '==> %s\n' "$*" | emit_out
}

run_cmd() {
	if command -v rtk >/dev/null 2>&1; then
		rtk "$@"
	else
		"$@"
	fi
}

run_cmd_quiet() {
	run_cmd "$@" >/dev/null
}

slug() {
	local value="${1:-unknown}"
	value="${value//\//-}"
	value="${value#-}"
	value="${value:-root}"
	printf '%s' "${value}"
}

init_output_dir() {
	local requested="${1:-}"
	if [[ -n "${requested}" ]]; then
		DEPS_OUTPUT_DIR="${requested}"
	else
		DEPS_OUTPUT_DIR="${SCRIPT_DIR}/output/audit-$(date +%Y%m%d-%H%M%S)"
	fi
	mkdir -p "${DEPS_OUTPUT_DIR}"
	export DEPS_OUTPUT_DIR
	DEPS_SUMMARY_FILE="${DEPS_OUTPUT_DIR}/summary.txt"
	MANIFEST_LINT_FILE="${DEPS_OUTPUT_DIR}/manifest-lint.txt"
	: >"${DEPS_SUMMARY_FILE}"
	: >"${MANIFEST_LINT_FILE}"
	write_summary_line "dependency pin audit summary"
	write_summary_line "repo: ${REPO_ROOT}"
	write_summary_line "output: ${DEPS_OUTPUT_DIR}"
	write_summary_line "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	write_summary_line ""
	info "writing reports to ${DEPS_OUTPUT_DIR}"
}

write_summary_line() {
	local line="$1"
	if [[ -n "${DEPS_SUMMARY_FILE}" ]]; then
		printf '%s\n' "${line}" >>"${DEPS_SUMMARY_FILE}"
	fi
}

report_path() {
	printf '%s/%s' "${DEPS_OUTPUT_DIR}" "$1"
}

append_manifest_lint() {
	local line="$1"
	if [[ -n "${MANIFEST_LINT_FILE}" ]]; then
		printf '%s\n' "${line}" >>"${MANIFEST_LINT_FILE}"
	fi
	printf '%s\n' "${line}" | emit_err
}

require_repo_root() {
	[[ -f "${REPO_ROOT}/pyproject.toml" ]] ||
		die "not a repo root: ${REPO_ROOT} (expected pyproject.toml)"
	[[ -f "${REPO_ROOT}/Cargo.toml" ]] ||
		die "not a repo root: ${REPO_ROOT} (expected Cargo.toml)"
	[[ -f "${REPO_ROOT}/sdk/c/CMakeLists.txt" ]] ||
		die "not a repo root: ${REPO_ROOT} (expected sdk/c/CMakeLists.txt)"
}

read_cargo_version() {
	grep -E '^version[[:space:]]*=' "${CYT_INDEXER_CARGO}" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

read_cmake_project_version() {
	grep -E '^project\(cyt-indexer-c VERSION ' "${REPO_ROOT}/sdk/c/CMakeLists.txt" |
		head -1 |
		sed -E 's/^project\(cyt-indexer-c VERSION ([^ ]+) .*/\1/'
}

record_failure() {
	local name="$1"
	local detail="$2"
	printf 'FAIL: %s: %s\n' "${name}" "${detail}" | emit_err
	write_summary_line "${name}: failed (${detail})"
	FAILURES=$((FAILURES + 1))
}

record_ok() {
	local name="$1"
	[[ "${SHORT}" != true ]] && printf 'ok: %s\n' "${name}" | emit_out
	write_summary_line "${name}: ok"
}

run_step() {
	local name="$1"
	shift
	[[ "${SHORT}" != true ]] && info "=== ${name} ==="
	if "$@"; then
		record_ok "${name}"
	else
		local status=$?
		[[ "${SHORT}" == true ]] && info "=== ${name} ==="
		record_failure "${name}" "exit ${status}"
		return "${status}"
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--output-dir)
		[[ $# -ge 2 ]] || die "--output-dir requires a path"
		OUTPUT_DIR="$2"
		shift 2
		;;
	--output-dir=*)
		OUTPUT_DIR="${1#*=}"
		shift
		;;
	--no-report)
		DO_REPORT=0
		shift
		;;
	--report)
		DO_REPORT=1
		shift
		;;
	--no-manifest-lint)
		DO_MANIFEST_LINT=0
		shift
		;;
	--manifest-lint)
		DO_MANIFEST_LINT=1
		shift
		;;
	--short)
		SHORT=true
		shift
		;;
	--skip)
		[[ $# -ge 2 ]] || die "--skip requires python|rust|npm|go|c"
		case "$2" in
		python) SKIP_PYTHON=1 ;;
		rust) SKIP_RUST=1 ;;
		npm) SKIP_NPM=1 ;;
		go) SKIP_GO=1 ;;
		c) SKIP_C=1 ;;
		*) die "unknown --skip target: $2 (expected python|rust|npm|go|c)" ;;
		esac
		shift 2
		;;
	--skip=*)
		case "${1#*=}" in
		python) SKIP_PYTHON=1 ;;
		rust) SKIP_RUST=1 ;;
		npm) SKIP_NPM=1 ;;
		go) SKIP_GO=1 ;;
		c) SKIP_C=1 ;;
		*) die "unknown --skip target: ${1#*=}" ;;
		esac
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: verify-pins.sh [options]

Verify lockfiles are in sync with manifests and (by default) flag loose direct
dependency ranges in pyproject.toml and package.json.

Rust exact versions live in Cargo.lock; the rust lock step runs
cargo metadata --locked (manifest semver ranges in Cargo.toml are not linted).

Only checks manifests and lockfiles in this repository (not git submodules or
tag-pinned worktrees such as chunk-your-tools-v*).

Writes pinned-version inventory and check results under:
  scripts/deps/output/audit-YYYYMMDD-HHMMSS/

Checked targets:
  pyproject.toml   uv.lock (repo root + sdk/python)
  Cargo.toml       Cargo.lock (workspace; cargo metadata --locked)
  sdk/c            CMakeLists.txt VERSION + synced cyt_indexer.h
  sdk/go           go.sum
  scripts/pre-commit-hooks/go-sdk-tools.sh (Go dev-tool pins via go install)
  package.json     package-lock.json (root + sdk/typescript)

Manifest lint (when enabled):
  pyproject.toml, package.json — exact pins required in manifests
  Cargo.toml — skipped; Cargo.lock is the pin (see rust lock step)

Options:
  --output-dir DIR      Directory for generated reports (default: timestamped)
  --report              Write audit reports (default)
  --no-report           Skip report files; console output only
  --short               Only print errors and warnings (hide passing steps)
  --manifest-lint       Check pyproject/package.json for loose ranges (default)
  --no-manifest-lint    Skip manifest range checks; lockfiles only
  --skip TARGET         Skip python, rust, npm, go, or c (repeatable)

Examples:
  ./scripts/deps/verify-pins.sh
  ./scripts/deps/verify-pins.sh --short
  ./scripts/deps/verify-pins.sh --no-manifest-lint
  ./scripts/deps/verify-pins.sh --output-dir /tmp/pin-audit
  ./scripts/deps/verify-pins.sh --skip go
EOF
		exit 0
		;;
	*)
		die "unknown arg: $1 (try --help)"
		;;
	esac
done

require_repo_root
[[ "${DO_REPORT}" -eq 1 ]] && init_output_dir "${OUTPUT_DIR}"

run_checked() {
	local out_file="$1"
	shift
	if [[ "${DO_REPORT}" -eq 1 ]]; then
		if "$@" >"${out_file}" 2>&1; then
			return 0
		fi
		emit_check_output "${out_file}"
		return 1
	fi
	if [[ "${SHORT}" == true ]]; then
		local err_file
		err_file="$(mktemp "${TMPDIR:-/tmp}/verify-pins.XXXXXX")"
		if "$@" >"${err_file}" 2>&1; then
			rm -f "${err_file}"
			return 0
		fi
		emit_check_output "${err_file}"
		rm -f "${err_file}"
		return 1
	fi
	"$@"
}

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

run_in_dir() {
	local dir="$1"
	shift
	(cd "${dir}" && run_cmd "$@")
}

report_python_inventory() {
	local label="$1"
	local project_dir="$2"
	local slug_name out_req out_pylock

	require_cmd uv
	slug_name="$(slug "${label}")"
	out_req="$(report_path "python-${slug_name}-requirements.txt")"
	out_pylock="$(report_path "pylock.${slug_name}.toml")"

	info "python ${label}: export pinned versions"
	(
		cd "${project_dir}"
		run_cmd_quiet uv export --frozen --all-extras --group dev --group test \
			--format requirements.txt --output-file "${out_req}"
		run_cmd_quiet uv export --frozen --all-extras --group dev --group test \
			--format pylock.toml --output-file "${out_pylock}"
	)
	write_summary_line "python ${label}: inventory -> python-${slug_name}-requirements.txt, pylock.${slug_name}.toml"
}

verify_python_lock() {
	local label="$1"
	local project_dir="$2"
	local slug_name out_check

	require_cmd uv
	[[ -f "${project_dir}/pyproject.toml" ]] ||
		die "python ${label}: missing pyproject.toml"
	[[ -f "${project_dir}/uv.lock" ]] ||
		die "python ${label}: missing uv.lock (run: uv lock --project ${project_dir})"

	slug_name="$(slug "${label}")"
	out_check=""
	[[ "${DO_REPORT}" -eq 1 ]] && out_check="$(report_path "python-${slug_name}-lock-check.txt")"

	info "python ${label}: uv lock --check"
	if run_checked "${out_check}" run_in_dir "${project_dir}" uv lock --check; then
		[[ "${DO_REPORT}" -eq 1 ]] &&
			write_summary_line "python ${label}: lock check -> python-${slug_name}-lock-check.txt"
		[[ "${DO_REPORT}" -eq 1 ]] && report_python_inventory "${label}" "${project_dir}"
		return 0
	fi
	return 1
}

report_rust_inventory() {
	local label="$1"
	local crate_dir="$2"
	local slug_name out_json out_tree

	require_cmd cargo
	slug_name="$(slug "${label}")"
	out_json="$(report_path "rust-${slug_name}-packages.json")"
	out_tree="$(report_path "rust-${slug_name}-tree.txt")"

	info "rust ${label}: export pinned packages"
	(
		cd "${crate_dir}"
		run_cmd cargo metadata --locked --format-version 1 --quiet \
			>"${out_json}.raw" 2>/dev/null
		if command -v jq >/dev/null 2>&1; then
			jq '[.packages[] | {name, version, source}] | sort_by(.name)' \
				"${out_json}.raw" >"${out_json}"
			rm -f "${out_json}.raw"
		else
			mv "${out_json}.raw" "${out_json}"
		fi
		run_cmd cargo tree --locked --prefix none >"${out_tree}" 2>/dev/null || true
	)
	write_summary_line "rust ${label}: inventory -> rust-${slug_name}-packages.json, rust-${slug_name}-tree.txt"
}

verify_rust_lock() {
	local label="$1"
	local crate_dir="$2"
	local slug_name out_check

	require_cmd cargo
	[[ -f "${crate_dir}/Cargo.toml" ]] ||
		die "rust ${label}: missing Cargo.toml"
	[[ -f "${crate_dir}/Cargo.lock" ]] ||
		die "rust ${label}: missing Cargo.lock (run: cargo generate-lockfile in ${crate_dir})"

	slug_name="$(slug "${label}")"
	out_check=""
	[[ "${DO_REPORT}" -eq 1 ]] && out_check="$(report_path "rust-${slug_name}-lock-check.txt")"

	info "rust ${label}: cargo metadata --locked"
	if run_checked "${out_check}" run_in_dir "${crate_dir}" cargo metadata --locked --format-version 1 --quiet; then
		[[ "${DO_REPORT}" -eq 1 ]] &&
			write_summary_line "rust ${label}: lock check -> rust-${slug_name}-lock-check.txt"
		[[ "${DO_REPORT}" -eq 1 ]] && report_rust_inventory "${label}" "${crate_dir}"
		return 0
	fi
	return 1
}

report_go_inventory() {
	local label="$1"
	local mod_dir="$2"
	local slug_name out_mods out_sum

	slug_name="$(slug "${label}")"
	out_mods="$(report_path "go-${slug_name}-modules.txt")"
	out_sum="$(report_path "go-${slug_name}-sum.txt")"

	info "go ${label}: export pinned modules"
	(
		cd "${mod_dir}"
		run_cmd go list -m all | sort >"${out_mods}"
		cp go.sum "${out_sum}"
	)
	write_summary_line "go ${label}: inventory -> go-${slug_name}-modules.txt, go-${slug_name}-sum.txt"
}

verify_go_lock() {
	local label="$1"
	local mod_dir="$2"
	local slug_name out_check

	slug_name="$(slug "${label}")"
	require_cmd go
	[[ -f "${mod_dir}/go.mod" ]] ||
		die "go ${label}: missing go.mod"
	[[ -f "${mod_dir}/go.sum" ]] ||
		die "go ${label}: missing go.sum (run: go mod tidy in ${mod_dir#"${REPO_ROOT}"/})"

	out_check=""
	[[ "${DO_REPORT}" -eq 1 ]] && out_check="$(report_path "go-${slug_name}-lock-check.txt")"

	info "go ${label}: go mod verify"
	if run_checked "${out_check}" run_in_dir "${mod_dir}" go mod verify; then
		[[ "${DO_REPORT}" -eq 1 ]] &&
			write_summary_line "go ${label}: lock check -> go-${slug_name}-lock-check.txt"
		[[ "${DO_REPORT}" -eq 1 ]] && report_go_inventory "${label}" "${mod_dir}"
		return 0
	fi
	return 1
}

verify_go_devtool_pins() {
	local pins_file="${REPO_ROOT}/scripts/pre-commit-hooks/go-sdk-tools.sh"
	local -a required=(
		GO_SDK_TOOL_GOFUMPT
		GO_SDK_TOOL_GOIMPORTS
		GO_SDK_TOOL_STATICCHECK
		GO_SDK_TOOL_GOCRITIC
		GO_SDK_TOOL_GOSEC
	)
	local var pattern

	require_cmd go
	[[ -f "${pins_file}" ]] ||
		die "go dev-tool pins: missing ${pins_file#"${REPO_ROOT}"/}"

	# shellcheck disable=SC1090
	source "${pins_file}"

	for var in "${required[@]}"; do
		pattern="${!var:-}"
		[[ -n "${pattern}" ]] ||
			die "go dev-tool pins: ${var} is unset in go-sdk-tools.sh"
		if [[ ! "${pattern}" =~ @v ]]; then
			die "go dev-tool pins: ${var} must pin an explicit @version (${pattern})"
		fi
	done

	if [[ -d "${REPO_ROOT}/sdk/go/tools" ]]; then
		die "go dev-tool pins: sdk/go/tools/ was removed; delete the leftover directory"
	fi

	record_ok "go dev-tool pins (go-sdk-tools.sh)"
}

report_npm_inventory() {
	local label="$1"
	local package_dir="$2"
	local slug_name out_json out_direct

	slug_name="$(slug "${label}")"
	out_json="$(report_path "npm-${slug_name}-packages.json")"
	out_direct="$(report_path "npm-${slug_name}-direct.json")"

	info "npm ${label}: export pinned packages"
	if command -v jq >/dev/null 2>&1; then
		jq -S '
			{
				root: .packages[""].version,
				packages: [
					.packages
					| to_entries[]
					| select(.key != "" and (.value.version? // null) != null)
					| {
						name: (.value.name // (.key | sub("^node_modules/"; ""))),
						version: .value.version,
						development: (.value.dev // false)
					}
				]
				| unique
				| sort_by(.name)
			}
		' "${package_dir}/package-lock.json" >"${out_json}"
		jq -S '
			{
				dependencies: (.packages[""].dependencies // {}),
				devDependencies: (.packages[""].devDependencies // {})
			}
		' "${package_dir}/package-lock.json" >"${out_direct}"
	else
		cp "${package_dir}/package-lock.json" "${out_json}"
	fi
	write_summary_line "npm ${label}: inventory -> npm-${slug_name}-packages.json, npm-${slug_name}-direct.json"
}

verify_npm_lock() {
	local label="$1"
	local package_dir="$2"
	local slug_name out_check

	require_cmd npm
	[[ -f "${package_dir}/package.json" ]] ||
		die "npm ${label}: missing package.json"
	[[ -f "${package_dir}/package-lock.json" ]] ||
		die "npm ${label}: missing package-lock.json (run: npm install in ${package_dir})"

	slug_name="$(slug "${label}")"
	out_check=""
	[[ "${DO_REPORT}" -eq 1 ]] && out_check="$(report_path "npm-${slug_name}-lock-check.txt")"

	info "npm ${label}: npm ci --dry-run"
	if run_checked "${out_check}" run_in_dir "${package_dir}" npm ci --dry-run --ignore-scripts; then
		[[ "${DO_REPORT}" -eq 1 ]] &&
			write_summary_line "npm ${label}: lock check -> npm-${slug_name}-lock-check.txt"
		[[ "${DO_REPORT}" -eq 1 ]] && report_npm_inventory "${label}" "${package_dir}"
		return 0
	fi
	return 1
}

report_c_inventory() {
	local c_dir="${REPO_ROOT}/sdk/c"
	local out_manifest out_version
	local cargo_version cmake_version cmake_min

	out_manifest="$(report_path "c-sdk-manifest.txt")"
	out_version="$(report_path "c-sdk-version.txt")"
	cargo_version="$(read_cargo_version)"
	cmake_version="$(read_cmake_project_version)"
	cmake_min="$(grep -E '^cmake_minimum_required\(VERSION ' "${c_dir}/CMakeLists.txt" |
		head -1 |
		sed -E 's/^cmake_minimum_required\(VERSION ([^)]+)\).*/\1/')"

	info "c sdk: export manifest inventory"
	{
		printf 'workspace_version=%s\n' "${cargo_version}"
		printf 'cmake_project_version=%s\n' "${cmake_version}"
		printf 'cmake_minimum_required=%s\n' "${cmake_min}"
		printf 'dependency_lockfile=Cargo.lock\n'
		printf 'build_script=sdk/c/scripts/build-c-lib.sh\n'
		printf 'public_header=sdk/c/include/cyt_indexer.h\n'
		printf 'supported_targets=\n'
		awk '
			/^_cyt_supported_targets/ { in_list=1; next }
			in_list && /^[[:space:]]*[a-z0-9_]+-/ { gsub(/^[[:space:]]+/, ""); print "  " $0 }
			in_list && /^\)/ { in_list=0 }
		' "${c_dir}/CMakeLists.txt"
	} >"${out_version}"
	cp "${c_dir}/CMakeLists.txt" "${out_manifest}"
	write_summary_line "c sdk: inventory -> c-sdk-version.txt, c-sdk-manifest.txt"
}

verify_c_sdk() {
	local c_dir="${REPO_ROOT}/sdk/c"
	local cmake_file="${c_dir}/CMakeLists.txt"
	local build_script="${c_dir}/scripts/build-c-lib.sh"
	local header_src="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt_indexer.h"
	local header_dest="${c_dir}/include/cyt_indexer.h"
	local cargo_version cmake_version out_check slug_name

	slug_name="$(slug "sdk-c")"
	out_check=""
	[[ "${DO_REPORT}" -eq 1 ]] && out_check="$(report_path "c-${slug_name}-check.txt")"

	[[ -f "${cmake_file}" ]] ||
		die "c sdk: missing CMakeLists.txt"
	[[ -f "${build_script}" ]] ||
		die "c sdk: missing scripts/build-c-lib.sh"
	[[ -x "${build_script}" ]] ||
		die "c sdk: build script not executable: ${build_script}"
	[[ -f "${header_dest}" ]] ||
		die "c sdk: missing include/cyt_indexer.h"
	[[ -f "${header_src}" ]] ||
		die "c sdk: missing sdk/rust/cyt-indexer/cyt_indexer.h (run: cargo build -p cyt-indexer --features ffi)"

	cargo_version="$(read_cargo_version)"
	cmake_version="$(read_cmake_project_version)"
	[[ -n "${cargo_version}" ]] ||
		die "c sdk: could not read sdk/rust/cyt-indexer/Cargo.toml version"
	[[ -n "${cmake_version}" ]] ||
		die "c sdk: could not read CMake project VERSION"

	info "c sdk: verify version sync and header"
	if [[ "${DO_REPORT}" -eq 1 ]]; then
		if ! {
			echo "cargo_version=${cargo_version}"
			echo "cmake_version=${cmake_version}"
			[[ "${cargo_version}" == "${cmake_version}" ]] ||
				{
					echo "version mismatch: sync with ./scripts/publish/sync-version.sh"
					exit 1
				}
			cmp -s "${header_src}" "${header_dest}" ||
				{
					echo "header out of sync: run bash sdk/c/scripts/build-c-lib.sh --sync-header"
					exit 1
				}
			echo "header synced"
			echo "ffi dependency lockfile: Cargo.lock (checked via rust lock step)"
		} >"${out_check}" 2>&1; then
			emit_check_output "${out_check}"
			return 1
		fi
	else
		[[ "${cargo_version}" == "${cmake_version}" ]] ||
			{
				printf 'c sdk: version mismatch: Cargo.toml=%s CMakeLists.txt=%s\n' \
					"${cargo_version}" "${cmake_version}" | emit_err
				return 1
			}
		cmp -s "${header_src}" "${header_dest}" ||
			{
				printf '%s\n' \
					"c sdk: header out of sync (run: bash sdk/c/scripts/build-c-lib.sh --sync-header)" |
					emit_err
				return 1
			}
	fi

	[[ "${DO_REPORT}" -eq 1 ]] &&
		write_summary_line "c sdk: check -> c-${slug_name}-check.txt"
	[[ "${DO_REPORT}" -eq 1 ]] && report_c_inventory
	return 0
}

lint_pyproject_ranges() {
	local label="$1"
	local file="$2"
	local lineno spec content
	local issues=0

	[[ -f "${file}" ]] || return 0

	while IFS= read -r content; do
		lineno="${content%%:*}"
		content="${content#*:}"
		[[ "${content}" =~ ^[[:space:]]*\"([^\"]+)\",[[:space:]]*$ ]] || continue
		spec="${BASH_REMATCH[1]}"
		[[ "${spec}" == *" @ "* ]] && continue
		[[ "${spec}" == *"=="* ]] && continue
		if [[ "${spec}" == *">="* || "${spec}" == *"~="* || "${spec}" == *"<="* ||
			"${spec}" == *"^"* || "${spec}" == *"<"* || "${spec}" == *">"* ]]; then
			append_manifest_lint "${file}:${lineno}: loose Python specifier: \"${spec}\""
			issues=$((issues + 1))
		fi
	done < <(grep -En '^[[:space:]]*"[^"]+",?$' "${file}" || true)

	while IFS= read -r content; do
		lineno="${content%%:*}"
		content="${content#*:}"
		if [[ "${content}" == *"requires"* && (
			"${content}" == *">="* || "${content}" == *"~="* || "${content}" == *"<="* ||
			"${content}" == *"^"* || "${content}" == *"<"* || "${content}" == *">"*) ]] \
			; then
			append_manifest_lint "${file}:${lineno}: loose build-system requires"
			issues=$((issues + 1))
		fi
	done < <(grep -En 'requires\s*=' "${file}" || true)

	if [[ "${issues}" -gt 0 ]]; then
		record_failure "manifest ${label}" "${issues} loose Python specifier(s)"
		return 1
	fi
	record_ok "manifest ${label}"
}

lint_package_json_ranges() {
	local label="$1"
	local file="$2"
	local lineno content
	local issues=0

	[[ -f "${file}" ]] || return 0

	while IFS= read -r content; do
		lineno="${content%%:*}"
		content="${content#*:}"
		if [[ "${content}" == *'"^'* || "${content}" == *'"~'* ||
			"${content}" == *'">='* || "${content}" == *'"<='* ||
			"${content}" == *'"<'* || "${content}" == *'"*'* ]]; then
			append_manifest_lint "${file}:${lineno}: loose npm range"
			issues=$((issues + 1))
		fi
	done < <(
		awk '
			/"(dependencies|devDependencies|peerDependencies|optionalDependencies)"/ { in_deps=1; next }
			in_deps && /^[[:space:]]*\}/ { in_deps=0; next }
			in_deps { print NR ":" $0 }
		' "${file}"
	)

	if [[ "${issues}" -gt 0 ]]; then
		record_failure "manifest ${label}" "${issues} loose npm range(s)"
		return 1
	fi
	record_ok "manifest ${label}"
}

lint_cmake_project_version() {
	local label="$1"
	local file="$2"
	local cargo_version cmake_version

	[[ -f "${file}" ]] || return 0

	cargo_version="$(read_cargo_version)"
	cmake_version="$(read_cmake_project_version)"
	if [[ -z "${cmake_version}" ]]; then
		append_manifest_lint "${file}: missing project(cyt-indexer-c VERSION ...)"
		record_failure "manifest ${label}" "missing project VERSION"
		return 1
	fi
	if [[ "${cargo_version}" != "${cmake_version}" ]]; then
		append_manifest_lint "${file}: VERSION ${cmake_version} != cyt-indexer Cargo.toml ${cargo_version}"
		record_failure "manifest ${label}" "version out of sync with cyt-indexer Cargo.toml"
		return 1
	fi
	record_ok "manifest ${label}"
}

verify_manifests() {
	local had_failure=0

	lint_pyproject_ranges "pyproject.toml (root)" "${REPO_ROOT}/pyproject.toml" || had_failure=1
	lint_pyproject_ranges "pyproject.toml (sdk/python)" "${REPO_ROOT}/sdk/python/pyproject.toml" || had_failure=1

	lint_package_json_ranges "package.json (root)" "${REPO_ROOT}/package.json" || had_failure=1
	lint_package_json_ranges "package.json (sdk/typescript)" "${REPO_ROOT}/sdk/typescript/package.json" || had_failure=1

	# Cargo.toml semver ranges are intentional; exact pins are in Cargo.lock
	# (verified by verify_rust_lock / cargo metadata --locked).
	record_ok "manifest Cargo.toml (cyt-indexer, pinned via Cargo.lock)"

	lint_cmake_project_version "CMakeLists.txt (sdk/c)" "${REPO_ROOT}/sdk/c/CMakeLists.txt" || had_failure=1

	if [[ "${DO_REPORT}" -eq 1 && -s "${MANIFEST_LINT_FILE}" ]]; then
		write_summary_line "manifest lint findings -> manifest-lint.txt"
	fi

	return "${had_failure}"
}

info "repo: ${REPO_ROOT}"

if [[ "${SKIP_PYTHON}" -eq 0 ]]; then
	run_step "python lock (root)" verify_python_lock "root" "${REPO_ROOT}"
fi

if [[ "${SKIP_RUST}" -eq 0 ]]; then
	run_step "rust lock (workspace)" verify_rust_lock "workspace" "${REPO_ROOT}"
fi

if [[ "${SKIP_C}" -eq 0 ]]; then
	run_step "c sdk (sdk/c)" verify_c_sdk
fi

if [[ "${SKIP_GO}" -eq 0 ]]; then
	run_step "go lock (sdk/go)" verify_go_lock "sdk-go" "${REPO_ROOT}/sdk/go"
	run_step "go dev-tool pins" verify_go_devtool_pins
fi

if [[ "${SKIP_NPM}" -eq 0 ]]; then
	run_step "npm lock (root)" verify_npm_lock "root" "${REPO_ROOT}"
	run_step "npm lock (sdk/typescript)" verify_npm_lock "sdk/typescript" "${REPO_ROOT}/sdk/typescript"
fi

if [[ "${DO_MANIFEST_LINT}" -eq 1 ]]; then
	run_step "manifest lint" verify_manifests
fi

if [[ "${SHORT}" != true ]]; then
	echo ""
fi
if [[ "${DO_REPORT}" -eq 1 ]]; then
	write_summary_line ""
	write_summary_line "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
	write_summary_line "failures: ${FAILURES}"
	[[ "${SHORT}" != true || "${FAILURES}" -gt 0 ]] &&
		info "summary: ${DEPS_OUTPUT_DIR}/summary.txt"
fi

if [[ "${FAILURES}" -gt 0 ]]; then
	die "${FAILURES} check(s) failed"
fi

printf '%s\n' "all pin checks passed" | emit_out
