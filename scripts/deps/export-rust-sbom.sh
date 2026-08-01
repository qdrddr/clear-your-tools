#!/usr/bin/env bash
# Export CycloneDX SBOM for cyt-indexer and Snyk SBOM test report.
#
# Generated from Cargo.lock (do not edit by hand):
#   sdk/rust/cyt-indexer/cyt-indexer.cdx.json   — CycloneDX SBOM (cargo cyclonedx)
#   sdk/rust/cyt-indexer/cyt-indexer.snyk.json  — Snyk SBOM test result
#
# Pre-commit auto-fix hooks exclude these paths (see .pre-commit-config.yaml);
# typos/codespell/detect-secrets/gitleaks exclusions are in typos.toml, pyproject.toml,
# .pre-commit-config.yaml; manual prettier uses .prettierignore.
#
# Usage:
#   ./scripts/deps/export-rust-sbom.sh
#   ./scripts/deps/export-rust-sbom.sh --check

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MANIFEST="${REPO_ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CDX_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
SNYK_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.snyk.json"
DO_CHECK=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--check)
		DO_CHECK=1
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: export-rust-sbom.sh [--check]

Writes sdk/rust/cyt-indexer/cyt-indexer.cdx.json and cyt-indexer.snyk.json.
With --check, fails if committed files differ from a fresh export.
EOF
		exit 0
		;;
	*)
		echo "error: unknown arg: $1 (try --help)" >&2
		exit 1
		;;
	esac
done

run_cmd() {
	if command -v rtk >/dev/null 2>&1; then
		rtk "$@"
	else
		"$@"
	fi
}

require_cmd() {
	local name="$1"
	command -v "${name}" >/dev/null 2>&1 || {
		echo "error: missing required command: ${name}" >&2
		exit 1
	}
}

sanitize_cdx() {
	local file="$1"
	local repo_root="$2"

	jq --arg root "${repo_root}" '
		.serialNumber = "urn:uuid:00000000-0000-0000-0000-000000000000"
		| .metadata.timestamp = "1970-01-01T00:00:00.000000000Z"
		| walk(
			if type == "string" then
				gsub("path\\+file:///[^#\"[:space:]]+/"; "path+file://")
				| gsub("file:///[^\"[:space:]]+/"; "file://")
				| gsub("file://" + $root + "/"; "file://")
				| gsub("file://" + $root; "file://.")
			else
				.
			end
		)
		| .metadata.properties = (
			[.metadata.properties[]? | select(.name != "cdx:rustc:sbom:target:triple")]
		)
	' "${file}"
}

assert_no_absolute_paths() {
	local file="$1"
	local label="$2"

	if grep -E -q '(/Volumes/|/Users/|[A-Za-z]:\\|file:///[A-Za-z])' "${file}"; then
		echo "error: ${label} still contains absolute paths after sanitization" >&2
		grep -E -n '(/Volumes/|/Users/|[A-Za-z]:\\|file:///[A-Za-z])' "${file}" | head -20 >&2
		exit 1
	fi
}

sanitize_snyk() {
	local repo_root="$1"

	jq --arg root "${repo_root}" '
		walk(
			if type == "string" then
				gsub("^" + $root + "/"; "")
				| gsub("^" + $root + "$"; ".")
				| gsub("^/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)+$"; ".")
			else
				.
			end
		)
	'
}

export_rust_sbom() {
	local cdx_out="$1"
	local snyk_out="$2"
	local cdx_tmp snyk_tmp snyk_status cdx_backup=""

	require_cmd cargo
	require_cmd jq
	require_cmd snyk
	[[ -f "${MANIFEST}" ]] || {
		echo "error: missing ${MANIFEST}" >&2
		exit 1
	}

	cdx_tmp="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-cdx.XXXXXX")"
	snyk_tmp="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-snyk.XXXXXX")"
	trap 'rm -f "${cdx_tmp}" "${snyk_tmp}" "${cdx_backup}"' RETURN

	if [[ "${cdx_out}" != "${CDX_FILE}" && -f "${CDX_FILE}" ]]; then
		cdx_backup="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-cdx-backup.XXXXXX")"
		cp "${CDX_FILE}" "${cdx_backup}"
	fi

	(
		cd "${REPO_ROOT}"
		run_cmd cargo cyclonedx --manifest-path sdk/rust/cyt-indexer/Cargo.toml --format json
	)

	[[ -f "${CDX_FILE}" ]] || {
		echo "error: cargo cyclonedx did not write ${CDX_FILE}" >&2
		exit 1
	}

	sanitize_cdx "${CDX_FILE}" "${REPO_ROOT}" | jq '.' >"${cdx_tmp}"
	assert_no_absolute_paths "${cdx_tmp}" "cyt-indexer.cdx.json"
	cp "${cdx_tmp}" "${cdx_out}"

	if [[ -n "${cdx_backup}" ]]; then
		cp "${cdx_backup}" "${CDX_FILE}"
	fi

	(
		cd "${REPO_ROOT}"
		run_cmd snyk sbom test \
			--file="sdk/rust/cyt-indexer/cyt-indexer.cdx.json" \
			--include-ignores \
			--json >"${snyk_tmp}" 2>/dev/null
	)
	snyk_status=$?
	if [[ "${snyk_status}" -gt 1 ]]; then
		echo "error: snyk sbom test failed (exit ${snyk_status})" >&2
		exit "${snyk_status}"
	fi
	if [[ "${snyk_status}" -eq 1 ]]; then
		echo "error: snyk sbom test found vulnerabilities (see ${snyk_out})" >&2
		jq '.' "${snyk_tmp}" >&2 || cat "${snyk_tmp}" >&2
		exit 1
	fi

	jq -S '.' "${snyk_tmp}" | sanitize_snyk "${REPO_ROOT}" >"${snyk_out}"
	assert_no_absolute_paths "${snyk_out}" "cyt-indexer.snyk.json"
}

files_match() {
	local expected="$1"
	local actual="$2"
	diff -q "${expected}" "${actual}" >/dev/null 2>&1
}

if [[ "${DO_CHECK}" -eq 1 ]]; then
	tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/export-rust-sbom.XXXXXX")"
	trap 'rm -rf "${tmp_dir}"' EXIT
	export_rust_sbom "${tmp_dir}/cyt-indexer.cdx.json" "${tmp_dir}/cyt-indexer.snyk.json"

	status=0
	for name in cyt-indexer.cdx.json cyt-indexer.snyk.json; do
		committed="${REPO_ROOT}/sdk/rust/cyt-indexer/${name}"
		generated="${tmp_dir}/${name}"
		if [[ ! -f "${committed}" ]]; then
			echo "error: missing ${committed} (run: ./scripts/deps/export-rust-sbom.sh)" >&2
			status=1
			continue
		fi
		if ! files_match "${committed}" "${generated}"; then
			echo "error: sdk/rust/cyt-indexer/${name} is out of sync (run: ./scripts/deps/export-rust-sbom.sh)" >&2
			status=1
		fi
	done
	exit "${status}"
fi

export_rust_sbom "${CDX_FILE}" "${SNYK_FILE}"
