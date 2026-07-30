#!/usr/bin/env bash
# Audit Rust crate licenses (cargo-deny policy + cargo-license inventory).
#
# Usage:
#   ./scripts/legal/audit-rust.sh [--output-dir DIR] [--check] [--report] [--skip-inventory]
#
# Targets:
#   - sdk/rust/cyt-indexer (workspace root)
#   - chunk-your-tools/ (standalone crate)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/legal/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

OUTPUT_DIR=""
DO_CHECK=1
DO_REPORT=1
SKIP_INVENTORY=0
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
	--install-tools)
		INSTALL_TOOLS=1
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
	--skip-inventory)
		SKIP_INVENTORY=1
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: audit-rust.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report] [--skip-inventory] [--install-tools]

Runs cargo-deny against each Rust crate and optionally writes cargo-license JSON.
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root
legal_require_cmd cargo
[[ "${INSTALL_TOOLS}" -eq 1 ]] && export LEGAL_INSTALL_TOOLS=1
legal_ensure_cargo_deny

if [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
elif [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
else
	legal_init_output_dir ""
fi

legal_audit_rust_crate() {
	local label="$1"
	local crate_dir="$2"
	local slug
	slug="$(legal_slug "${label}")"

	legal_info "rust ${label}: cargo deny"
	if [[ "${DO_CHECK}" -eq 1 ]]; then
		if ! (
			cd "${crate_dir}"
			legal_cargo_deny --all-features check licenses \
				>"${LEGAL_OUTPUT_DIR}/rust-deny-${slug}.txt" 2>&1
		); then
			cat "${LEGAL_OUTPUT_DIR}/rust-deny-${slug}.txt" >&2
			legal_die "rust ${label}: cargo deny failed"
		fi
		legal_write_summary_line "rust ${label}: cargo deny ok"
	fi

	if [[ "${DO_REPORT}" -eq 1 && "${SKIP_INVENTORY}" -eq 0 ]]; then
		if command -v cargo-license >/dev/null 2>&1; then
			legal_info "rust ${label}: cargo license"
			(
				cd "${crate_dir}"
				legal_run cargo license --json >"${LEGAL_OUTPUT_DIR}/rust-license-${slug}.json"
			)
			legal_write_summary_line "rust ${label}: cargo license -> rust-license-${slug}.json"
		else
			legal_info "rust ${label}: skipping cargo license (install with: cargo install cargo-license)"
			legal_write_summary_line "rust ${label}: cargo license skipped (cargo-license not installed)"
		fi
	fi
}

legal_audit_rust_crate "cyt-indexer" "${LEGAL_REPO_ROOT}"
if [[ -f "${LEGAL_REPO_ROOT}/chunk-your-tools/Cargo.toml" ]]; then
	legal_audit_rust_crate "chunk-your-tools" "${LEGAL_REPO_ROOT}/chunk-your-tools"
fi
