#!/usr/bin/env bash
# shellcheck shell=bash
# Shared helpers for scripts/legal/* (source scripts/legal/lib/common.sh).

if [[ -z "${LEGAL_LIB_SOURCED:-}" ]]; then
	LEGAL_LIB_SOURCED=1

	LEGAL_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
	LEGAL_REPO_ROOT="$(cd "${LEGAL_SCRIPT_DIR}/../.." && pwd)"

	# Mirrors deny.toml [licenses].allow (semicolon-separated for license-checker).
	LEGAL_ALLOWED_LICENSES="MIT;Apache-2.0;Apache-2.0 WITH LLVM-exception;BSD-2-Clause;BSD-3-Clause;0BSD;ISC;MPL-2.0;Unicode-3.0;Unlicense;Zlib"

	legal_allowed_licenses_csv() {
		printf '%s' "${LEGAL_ALLOWED_LICENSES}"
	}

	legal_license_allowed() {
		case ";${LEGAL_ALLOWED_LICENSES};" in
		*";$1;"*) return 0 ;;
		*) return 1 ;;
		esac
	}

	legal_die() {
		echo "error: $*" >&2
		exit 1
	}

	legal_info() {
		echo "==> $*"
	}

	legal_require_cmd() {
		command -v "$1" >/dev/null 2>&1 || legal_die "missing required command: $1"
	}

	# Use rtk when available (repo convention); otherwise run directly.
	legal_run() {
		if command -v rtk >/dev/null 2>&1; then
			rtk "$@"
		else
			"$@"
		fi
	}

	legal_prepend_path() {
		local dir="$1"
		[[ -d "${dir}" ]] || return 0
		case ":${PATH}:" in
		*":${dir}:"*) ;;
		*) PATH="${dir}:${PATH}" ;;
		esac
		export PATH
	}

	legal_cargo_bin_dir() {
		if command -v cargo >/dev/null 2>&1; then
			dirname "$(command -v cargo)"
			return 0
		fi
		if [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
			printf '%s\n' "${HOME}/.cargo/bin"
			return 0
		fi
		return 1
	}

	legal_cargo_deny_available() {
		legal_prepend_path "$(legal_cargo_bin_dir 2>/dev/null || true)"
		command -v cargo-deny >/dev/null 2>&1 && return 0
		cargo deny --version >/dev/null 2>&1
	}

	legal_cargo_deny() {
		legal_prepend_path "$(legal_cargo_bin_dir 2>/dev/null || true)"
		if command -v cargo-deny >/dev/null 2>&1; then
			cargo-deny "$@"
		elif cargo deny --version >/dev/null 2>&1; then
			cargo deny "$@"
		else
			legal_die "missing cargo-deny (install: cargo install cargo-deny --locked --version 0.19.8, or re-run with --install-tools)"
		fi
	}

	legal_install_cargo_deny() {
		legal_require_cmd cargo
		legal_info "installing cargo-deny 0.19.8 (one-time)"
		legal_run cargo install cargo-deny --locked --version 0.19.8
		legal_prepend_path "$(legal_cargo_bin_dir)"
		legal_cargo_deny --version >/dev/null 2>&1 ||
			legal_die "cargo-deny install finished but binary is unavailable"
	}

	legal_ensure_cargo_deny() {
		if legal_cargo_deny_available; then
			return 0
		fi
		if [[ "${LEGAL_INSTALL_TOOLS:-0}" -eq 1 ]]; then
			legal_install_cargo_deny
			return 0
		fi
		legal_die "missing cargo-deny (install: cargo install cargo-deny --locked --version 0.19.8, or re-run with --install-tools)"
	}

	legal_slug() {
		local value="${1:-unknown}"
		value="${value//\//-}"
		value="${value#-}"
		value="${value:-root}"
		printf '%s' "${value}"
	}

	legal_init_output_dir() {
		local requested="${1:-}"
		if [[ -n "${requested}" ]]; then
			LEGAL_OUTPUT_DIR="${requested}"
		else
			LEGAL_OUTPUT_DIR="${LEGAL_SCRIPT_DIR}/output/audit-$(date +%Y%m%d-%H%M%S)"
		fi
		mkdir -p "${LEGAL_OUTPUT_DIR}"
		export LEGAL_OUTPUT_DIR
		legal_info "writing reports to ${LEGAL_OUTPUT_DIR}"
	}

	legal_require_repo_root() {
		[[ -f "${LEGAL_REPO_ROOT}/pyproject.toml" ]] ||
			legal_die "not a repo root: ${LEGAL_REPO_ROOT}"
		[[ -f "${LEGAL_REPO_ROOT}/deny.toml" ]] ||
			legal_die "missing deny.toml at ${LEGAL_REPO_ROOT}"
	}

	legal_write_summary_line() {
		local line="$1"
		if [[ -n "${LEGAL_SUMMARY_FILE:-}" ]]; then
			printf '%s\n' "${line}" >>"${LEGAL_SUMMARY_FILE}"
		fi
	}

	legal_begin_summary() {
		LEGAL_SUMMARY_FILE="${LEGAL_OUTPUT_DIR}/summary.txt"
		: >"${LEGAL_SUMMARY_FILE}"
		legal_write_summary_line "license audit summary"
		legal_write_summary_line "repo: ${LEGAL_REPO_ROOT}"
		legal_write_summary_line "output: ${LEGAL_OUTPUT_DIR}"
		legal_write_summary_line "started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
		legal_write_summary_line ""
	}

	legal_end_summary() {
		legal_write_summary_line ""
		legal_write_summary_line "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
		legal_info "summary: ${LEGAL_SUMMARY_FILE}"
	}

fi
