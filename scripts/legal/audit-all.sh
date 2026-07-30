#!/usr/bin/env bash
# Run license audits for all ecosystems in this monorepo.
#
# Usage:
#   ./scripts/legal/audit-all.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report]
#   ./scripts/legal/audit-all.sh --skip rust --skip npm
#   ./scripts/legal/audit-all.sh --with-dev
#   ./scripts/legal/audit-all.sh --skip sdk-c
#
# Ecosystems: rust, python, npm, go, sdk-c (sdk/c CMake wrapper)
#
# Writes reports under scripts/legal/output/audit-YYYYMMDD-HHMMSS/ by default.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/legal/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

OUTPUT_DIR=""
DO_CHECK=1
DO_REPORT=1
WITH_DEV=0
SKIP_RUST=0
SKIP_PYTHON=0
SKIP_NPM=0
SKIP_GO=0
SKIP_SDK_C=0
INSTALL_TOOLS=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--output-dir)
		[[ $# -ge 2 ]] || legal_die "--output-dir requires a path"
		OUTPUT_DIR="$2"
		shift 2
		;;
	--output-dir=*)
		OUTPUT_DIR="${1#*=}"
		shift
		;;
	--check)
		DO_CHECK=1
		shift
		;;
	--no-check)
		DO_CHECK=0
		shift
		;;
	--report)
		DO_REPORT=1
		shift
		;;
	--no-report)
		DO_REPORT=0
		shift
		;;
	--with-dev)
		WITH_DEV=1
		shift
		;;
	--install-tools)
		INSTALL_TOOLS=1
		shift
		;;
	--skip)
		[[ $# -ge 2 ]] || legal_die "--skip requires rust|python|npm|go|sdk-c|c"
		case "$2" in
		rust) SKIP_RUST=1 ;;
		python) SKIP_PYTHON=1 ;;
		npm) SKIP_NPM=1 ;;
		go) SKIP_GO=1 ;;
		sdk-c | c) SKIP_SDK_C=1 ;;
		*) legal_die "unknown --skip target: $2 (expected rust|python|npm|go|sdk-c|c)" ;;
		esac
		shift 2
		;;
	--skip=*)
		case "${1#*=}" in
		rust) SKIP_RUST=1 ;;
		python) SKIP_PYTHON=1 ;;
		npm) SKIP_NPM=1 ;;
		go) SKIP_GO=1 ;;
		sdk-c | c) SKIP_SDK_C=1 ;;
		*) legal_die "unknown --skip target: ${1#*=}" ;;
		esac
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: audit-all.sh [options]

Options:
  --output-dir DIR   Directory for generated reports (default: scripts/legal/output/audit-TIMESTAMP)
  --check            Enforce license policy where supported (default)
  --no-check         Skip policy checks; reports only
  --report           Write license reports (default)
  --no-report        Skip report generation
  --with-dev         Include dev dependencies for Python/npm audits
  --install-tools    Install missing audit tools (e.g. cargo-deny) before running
  --skip TARGET      Skip rust, python, npm, go, or sdk-c (alias: c) (repeatable)

Examples:
  ./scripts/legal/audit-all.sh
  ./scripts/legal/audit-all.sh --install-tools
  ./scripts/legal/audit-all.sh --output-dir /tmp/license-audit
  ./scripts/legal/audit-all.sh --no-check --with-dev
  ./scripts/legal/audit-all.sh --skip go
  ./scripts/legal/audit-all.sh --skip sdk-c
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root
legal_init_output_dir "${OUTPUT_DIR}"
export LEGAL_OUTPUT_DIR
legal_begin_summary

common_args=()
[[ -n "${LEGAL_OUTPUT_DIR}" ]] && common_args+=(--output-dir "${LEGAL_OUTPUT_DIR}")
[[ "${DO_CHECK}" -eq 0 ]] && common_args+=(--no-check)
[[ "${DO_REPORT}" -eq 0 ]] && common_args+=(--no-report)

python_args=("${common_args[@]}")
npm_args=("${common_args[@]}")
rust_args=("${common_args[@]}")
[[ "${WITH_DEV}" -eq 1 ]] && python_args+=(--with-dev)
[[ "${WITH_DEV}" -eq 1 ]] && npm_args+=(--with-dev)
[[ "${INSTALL_TOOLS}" -eq 1 ]] && rust_args+=(--install-tools)

run_step() {
	local name="$1"
	shift
	legal_info "=== ${name} ==="
	if "$@"; then
		legal_write_summary_line "${name}: ok"
	else
		local status=$?
		legal_write_summary_line "${name}: failed (exit ${status})"
		return "${status}"
	fi
}

if [[ "${SKIP_RUST}" -eq 0 ]]; then
	run_step "rust" bash "${SCRIPT_DIR}/audit-rust.sh" "${rust_args[@]}"
fi

if [[ "${SKIP_PYTHON}" -eq 0 ]]; then
	run_step "python" bash "${SCRIPT_DIR}/audit-python.sh" "${python_args[@]}"
fi

if [[ "${SKIP_NPM}" -eq 0 ]]; then
	run_step "npm" bash "${SCRIPT_DIR}/audit-npm.sh" "${npm_args[@]}"
fi

if [[ "${SKIP_GO}" -eq 0 ]]; then
	run_step "go" bash "${SCRIPT_DIR}/audit-go.sh" "${common_args[@]}"
fi

if [[ "${SKIP_SDK_C}" -eq 0 ]]; then
	run_step "sdk-c" bash "${SCRIPT_DIR}/audit-c.sh" "${common_args[@]}"
fi

legal_end_summary
legal_info "done"
