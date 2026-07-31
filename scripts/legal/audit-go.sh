#!/usr/bin/env bash
# Audit Go module dependency licenses via go-licenses.
#
# Usage:
#   ./scripts/legal/audit-go.sh [--output-dir DIR] [--check] [--report]
#
# Target: sdk/go/ (runtime module; dev linters use go install via go-sdk-tools.sh)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/legal/lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

OUTPUT_DIR=""
DO_CHECK=1
DO_REPORT=1

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
	-h | --help)
		cat <<'EOF'
Usage: audit-go.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report]

Downloads Go modules and writes CSV license reports for sdk/go.
First-party module metadata is taken from sdk/go/LICENSE (go-licenses ignores it).
Dev linters (staticcheck, gosec, etc.) are installed via scripts/pre-commit-hooks/go-sdk-tools.sh
and are not part of the audited runtime module.
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root
legal_require_cmd go

if [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
elif [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
else
	legal_init_output_dir ""
fi

legal_go_licenses() {
	if command -v go-licenses >/dev/null 2>&1; then
		go-licenses "$@"
	elif [[ -x "${GOPATH:-${HOME}/go}/bin/go-licenses" ]]; then
		"${GOPATH:-${HOME}/go}/bin/go-licenses" "$@"
	else
		legal_run go run github.com/google/go-licenses@v1.6.0 "$@"
	fi
}

audit_go_module() {
	local label="$1"
	local mod_dir="$2"
	local csv_basename="$3"

	[[ -f "${mod_dir}/go.mod" ]] || legal_die "missing ${mod_dir}/go.mod"

	local module_path
	module_path="$(
		awk '/^module / { print $2; exit }' "${mod_dir}/go.mod"
	)"
	[[ -n "${module_path}" ]] || legal_die "could not parse module path from ${mod_dir}/go.mod"

	legal_info "go ${label}: go mod download"
	(
		cd "${mod_dir}"
		legal_run go mod download
	)

	if [[ "${DO_REPORT}" -eq 1 ]]; then
		legal_info "go ${label}: go-licenses report (third-party deps)"
		(
			cd "${mod_dir}"
			set +e
			legal_go_licenses report ./... \
				--ignore="${module_path}" \
				>"${LEGAL_OUTPUT_DIR}/${csv_basename}.raw.csv" \
				2>"${LEGAL_OUTPUT_DIR}/${csv_basename}.stderr"
			go_status=$?
			set -e
			if [[ "${go_status}" -ne 0 ]] && [[ ! -s "${LEGAL_OUTPUT_DIR}/${csv_basename}.raw.csv" ]]; then
				: >"${LEGAL_OUTPUT_DIR}/${csv_basename}.raw.csv"
				legal_info "go ${label}: go-licenses exited ${go_status}; continuing with first-party LICENSE"
			fi
		)

		if [[ "${label}" == "sdk/go" ]]; then
			legal_info "go ${label}: enrich first-party LICENSE"
			legal_require_cmd python3
			python3 "${SCRIPT_DIR}/lib/enrich-go-licenses.py" \
				"${mod_dir}" "${LEGAL_REPO_ROOT}" "${LEGAL_OUTPUT_DIR}"
		fi
		legal_write_summary_line "go ${label}: go-licenses -> ${csv_basename}.csv"
	fi

	if [[ "${DO_CHECK}" -eq 1 && "${label}" == "sdk/go" ]]; then
		legal_info "go ${label}: license policy check"
		legal_require_cmd python3
		python3 - "${mod_dir}" "${LEGAL_ALLOWED_LICENSES}" <<'PY'
import sys
from pathlib import Path

go_dir = Path(sys.argv[1])
allowed = {item.strip() for item in sys.argv[2].split(";") if item.strip()}

license_file = None
for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"):
    candidate = go_dir / name
    if candidate.is_file():
        license_file = candidate
        break
if license_file is None:
    raise SystemExit(f"missing LICENSE under {go_dir}")

text = license_file.read_text(encoding="utf-8")
if "Apache License" in text and "Version 2.0" in text:
    license_id = "Apache-2.0"
else:
    raise SystemExit(f"could not identify license in {license_file}")

if license_id not in allowed:
    raise SystemExit(f"{go_dir}: license {license_id!r} not in allow-list")

print(f"sdk/go: {license_id} ({license_file})")
PY
		legal_write_summary_line "go ${label}: LICENSE present and allowed"
	fi
}

GO_MODULE_DIR="${LEGAL_REPO_ROOT}/sdk/go"

audit_go_module "sdk/go" "${GO_MODULE_DIR}" "go-sdk"
