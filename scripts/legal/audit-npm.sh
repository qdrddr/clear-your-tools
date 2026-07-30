#!/usr/bin/env bash
# Audit npm dependency licenses via license-checker.
#
# Usage:
#   ./scripts/legal/audit-npm.sh [--output-dir DIR] [--check] [--report] [--with-dev]
#
# Scans package trees that have package-lock.json:
#   - ./
#   - sdk/typescript/
#   - sdk/typescript/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/legal/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

OUTPUT_DIR=""
DO_CHECK=1
DO_REPORT=1
WITH_DEV=0

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
	-h | --help)
		cat <<'EOF'
Usage: audit-npm.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report] [--with-dev]

Runs license-checker for each npm project with a lockfile. Policy allow-list matches deny.toml.
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root
legal_require_cmd npm
legal_require_cmd npx

if [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
elif [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
else
	legal_init_output_dir ""
fi

NPM_PROJECTS=(
	"."
	"sdk/typescript"
)

legal_audit_npm_project() {
	local rel_dir="$1"
	local project_dir="${LEGAL_REPO_ROOT}/${rel_dir}"
	local slug
	slug="$(legal_slug "${rel_dir}")"
	local checker_args=(--json)

	[[ -f "${project_dir}/package.json" ]] || return 0
	[[ -f "${project_dir}/package-lock.json" ]] ||
		legal_die "npm ${rel_dir}: missing package-lock.json (run npm install first)"

	if [[ "${WITH_DEV}" -eq 0 ]]; then
		checker_args+=(--production)
	fi

	legal_info "npm ${rel_dir}: npm ci"
	(
		cd "${project_dir}"
		legal_run env -u npm_config_devdir npm ci
	)

	if [[ "${DO_REPORT}" -eq 1 ]]; then
		legal_info "npm ${rel_dir}: license-checker report"
		(
			cd "${project_dir}"
			legal_run npx --yes license-checker "${checker_args[@]}" \
				>"${LEGAL_OUTPUT_DIR}/npm-${slug}.json"
		)
		(
			cd "${project_dir}"
			legal_run npx --yes license-checker --summary "${checker_args[@]}" \
				>"${LEGAL_OUTPUT_DIR}/npm-${slug}-summary.txt"
		)
		legal_write_summary_line "npm ${rel_dir}: license-checker -> npm-${slug}.{json,summary.txt}"
	fi

	if [[ "${DO_CHECK}" -eq 1 ]]; then
		legal_info "npm ${rel_dir}: license-checker policy check"
		(
			cd "${project_dir}"
			legal_run npx --yes license-checker \
				--onlyAllow "$(legal_allowed_licenses_csv)" \
				"${checker_args[@]}" \
				>/dev/null
		)
		legal_write_summary_line "npm ${rel_dir}: licenses within allow-list"
	fi
}

for rel_dir in "${NPM_PROJECTS[@]}"; do
	legal_audit_npm_project "${rel_dir}"
done
