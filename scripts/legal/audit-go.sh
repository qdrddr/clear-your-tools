#!/usr/bin/env bash
# Audit Go module dependency licenses via go-licenses.
#
# Usage:
#   ./scripts/legal/audit-go.sh [--output-dir DIR] [--check] [--report]
#
# Target: sdk/go/

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

Downloads Go modules and writes a CSV license report for sdk/go.
First-party module metadata is taken from sdk/go/LICENSE (go-licenses ignores it).
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

GO_MODULE_DIR="${LEGAL_REPO_ROOT}/sdk/go"
[[ -f "${GO_MODULE_DIR}/go.mod" ]] || legal_die "missing ${GO_MODULE_DIR}/go.mod"

if [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
elif [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
else
	legal_init_output_dir ""
fi

GO_MODULE_PATH="$(
	awk '/^module / { print $2; exit }' "${GO_MODULE_DIR}/go.mod"
)"
[[ -n "${GO_MODULE_PATH}" ]] || legal_die "could not parse module path from ${GO_MODULE_DIR}/go.mod"

legal_go_licenses() {
	if command -v go-licenses >/dev/null 2>&1; then
		go-licenses "$@"
	elif [[ -x "${GOPATH:-${HOME}/go}/bin/go-licenses" ]]; then
		"${GOPATH:-${HOME}/go}/bin/go-licenses" "$@"
	else
		legal_run go run github.com/google/go-licenses@v1.6.0 "$@"
	fi
}

legal_info "go sdk/go: go mod download"
(
	cd "${GO_MODULE_DIR}"
	legal_run go mod download
)

if [[ "${DO_REPORT}" -eq 1 ]]; then
	legal_info "go sdk/go: go-licenses report (third-party deps)"
	(
		cd "${GO_MODULE_DIR}"
		set +e
		legal_go_licenses report ./... \
			--ignore="${GO_MODULE_PATH}" \
			>"${LEGAL_OUTPUT_DIR}/go-sdk.raw.csv" \
			2>"${LEGAL_OUTPUT_DIR}/go-sdk.stderr"
		go_status=$?
		set -e
		if [[ "${go_status}" -ne 0 ]] && [[ ! -s "${LEGAL_OUTPUT_DIR}/go-sdk.raw.csv" ]]; then
			: >"${LEGAL_OUTPUT_DIR}/go-sdk.raw.csv"
			legal_info "go sdk/go: go-licenses exited ${go_status}; continuing with first-party LICENSE"
		fi
	)

	legal_info "go sdk/go: enrich first-party LICENSE"
	legal_require_cmd python3
	python3 "${SCRIPT_DIR}/lib/enrich-go-licenses.py" \
		"${GO_MODULE_DIR}" "${LEGAL_REPO_ROOT}" "${LEGAL_OUTPUT_DIR}"
	legal_write_summary_line "go sdk/go: go-licenses -> go-sdk.csv (+ go-sdk-first-party.json)"
fi

if [[ "${DO_CHECK}" -eq 1 ]]; then
	legal_info "go sdk/go: license policy check"
	legal_require_cmd python3
	python3 - "${GO_MODULE_DIR}" "${LEGAL_ALLOWED_LICENSES}" <<'PY'
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
	legal_write_summary_line "go sdk/go: LICENSE present and allowed"
fi
