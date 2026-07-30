#!/usr/bin/env bash
# Audit Python dependency licenses via pip-licenses.
#
# Usage:
#   ./scripts/legal/audit-python.sh [--output-dir DIR] [--check] [--report] [--with-dev]
#
# Uses an isolated venv under the audit output directory so the repo .venv is untouched.
#
# Targets:
#   - repo root (clear-your-tools app; uv.lock)
#   - sdk/python (cyt-indexer-sdk dev env)

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
Usage: audit-python.sh [--output-dir DIR] [--check] [--no-check] [--report] [--no-report] [--with-dev]

Syncs each Python project into an isolated audit venv and runs pip-licenses.
By default only production/runtime packages are listed (--with-dev includes dev groups).
EOF
		exit 0
		;;
	*)
		legal_die "unknown arg: $1 (try --help)"
		;;
	esac
done

legal_require_repo_root
legal_require_cmd uv

if [[ -n "${LEGAL_OUTPUT_DIR:-}" ]]; then
	:
elif [[ -n "${OUTPUT_DIR}" ]]; then
	legal_init_output_dir "${OUTPUT_DIR}"
else
	legal_init_output_dir ""
fi

legal_python_venv_for() {
	local slug="$1"
	printf '%s/.venv-%s' "${LEGAL_OUTPUT_DIR}" "${slug}"
}

legal_python_sync_args() {
	local project_dir="$1"
	local -n _out=$2
	_out=()
	[[ "${WITH_DEV}" -eq 1 ]] && return 0

	_out+=(--no-dev)
	local groups_file="${project_dir}/pyproject.toml"
	if grep -qE '^[[:space:]]*dev[[:space:]]*=' "${groups_file}"; then
		_out+=(--no-group dev)
	fi
	if grep -qE '^[[:space:]]*test[[:space:]]*=' "${groups_file}"; then
		_out+=(--no-group test)
	fi
}

legal_python_run_in_project() {
	local project_dir="$1"
	local venv_dir="$2"
	shift 2
	local saved_uv="${UV_PROJECT_ENVIRONMENT:-}"
	export UV_PROJECT_ENVIRONMENT="${venv_dir}"
	local status=0

	pushd "${project_dir}" >/dev/null || legal_die "cd failed: ${project_dir}"
	legal_run "$@" || status=$?
	popd >/dev/null || true

	if [[ -n "${saved_uv}" ]]; then
		export UV_PROJECT_ENVIRONMENT="${saved_uv}"
	else
		unset UV_PROJECT_ENVIRONMENT
	fi
	return "${status}"
}

legal_audit_python_project() {
	local label="$1"
	local project_dir="$2"
	local sync_args=()
	local license_args=(--with-urls --with-license-file)
	local slug venv_dir venv_python pip_licenses
	slug="$(legal_slug "${label}")"
	venv_dir="$(legal_python_venv_for "${slug}")"
	venv_python="${venv_dir}/bin/python"
	pip_licenses="${venv_dir}/bin/pip-licenses"

	[[ -f "${project_dir}/pyproject.toml" ]] ||
		legal_die "missing pyproject.toml for ${label}: ${project_dir}"

	legal_python_sync_args "${project_dir}" sync_args

	legal_info "python ${label}: uv sync (isolated venv)"
	if [[ -f "${project_dir}/uv.lock" && "${project_dir}" == "${LEGAL_REPO_ROOT}" ]]; then
		legal_python_run_in_project "${project_dir}" "${venv_dir}" \
			uv sync --locked "${sync_args[@]}"
	else
		legal_python_run_in_project "${project_dir}" "${venv_dir}" \
			uv sync "${sync_args[@]}"
	fi

	legal_info "python ${label}: install pip-licenses"
	legal_python_run_in_project "${project_dir}" "${venv_dir}" \
		uv pip install --python "${venv_python}" pip-licenses
	[[ -x "${pip_licenses}" ]] ||
		legal_die "python ${label}: pip-licenses not found in ${venv_dir}"

	if [[ "${DO_REPORT}" -eq 1 ]]; then
		legal_info "python ${label}: pip-licenses report"
		"${pip_licenses}" --format=json "${license_args[@]}" \
			>"${LEGAL_OUTPUT_DIR}/python-${slug}.json"
		"${venv_python}" "${SCRIPT_DIR}/lib/enrich-python-licenses.py" \
			"${project_dir}" "${LEGAL_REPO_ROOT}" \
			"${LEGAL_OUTPUT_DIR}/python-${slug}.json"
		legal_write_summary_line "python ${label}: pip-licenses -> python-${slug}.{md,json}"
	fi

	if [[ "${DO_CHECK}" -eq 1 ]]; then
		legal_info "python ${label}: pip-licenses policy check"
		local tmp_json unknown_output check_status
		tmp_json="$(mktemp "${TMPDIR:-/tmp}/legal-python.XXXXXX.json")"
		"${pip_licenses}" --format=json "${license_args[@]}" >"${tmp_json}"
		"${venv_python}" "${SCRIPT_DIR}/lib/enrich-python-licenses.py" \
			"${project_dir}" "${LEGAL_REPO_ROOT}" "${tmp_json}" --json-only
		set +e
		unknown_output="$(
			"${venv_python}" - "${tmp_json}" <<'PY'
import json
import sys

FIRST_PARTY = {
    "clear-your-tools",
    "cyt-indexer-sdk",
    "chunk-your-tools",
    "chunk-your-skills",
}


def has_license_evidence(row: dict) -> bool:
    license_field = str(row.get("License", "")).strip()
    if license_field and license_field.upper() not in ("", "UNKNOWN"):
        return True
    license_file = str(row.get("LicenseFile", "")).strip()
    if license_file and license_file.upper() != "UNKNOWN":
        return True
    license_text = str(row.get("LicenseText", "")).strip()
    return bool(license_text and license_text.upper() != "UNKNOWN")


with open(sys.argv[1], encoding="utf-8") as handle:
    rows = json.load(handle)

unknown = [
    row
    for row in rows
    if row.get("Name") not in FIRST_PARTY and not has_license_evidence(row)
]
if unknown:
    for row in unknown:
        print(f"{row.get('Name')} ({row.get('Version')}): {row.get('License')!r}")
    sys.exit(len(unknown))
print("0")
PY
		)"
		check_status=$?
		set -e
		rm -f "${tmp_json}"
		if [[ "${check_status}" -ne 0 ]]; then
			echo "${unknown_output}" >&2
			legal_die "python ${label}: ${check_status} package(s) with UNKNOWN license"
		fi
		legal_write_summary_line "python ${label}: no UNKNOWN licenses"
	fi
}

legal_audit_python_project "root" "${LEGAL_REPO_ROOT}"
legal_audit_python_project "sdk-python" "${LEGAL_REPO_ROOT}/sdk/python"
