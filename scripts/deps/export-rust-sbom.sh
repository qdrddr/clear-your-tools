#!/usr/bin/env bash
# Export CycloneDX SBOM for cyt-indexer (pin verification) and optional Snyk report.
#
# Generated from Cargo.lock (do not edit by hand):
#   sdk/rust/cyt-indexer/cyt-indexer.cdx.json   — CycloneDX SBOM (cargo cyclonedx)
#   sdk/rust/cyt-indexer/cyt-indexer.snyk.json  — optional local Snyk SBOM snapshot
#
# CI and verify-pins only check cyt-indexer.cdx.json. Snyk Cloud scans the repo;
# the Snyk CLI is optional for local export when installed.
#
# Usage:
#   ./scripts/deps/export-rust-sbom.sh
#   ./scripts/deps/export-rust-sbom.sh --check

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${REPO_ROOT}/scripts/lib/chunk-worktree.sh"

MANIFEST="${REPO_ROOT}/sdk/rust/cyt-indexer/Cargo.toml"
CDX_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
SNYK_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.snyk.json"
DO_CHECK=0
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
	case "$1" in
	--check)
		DO_CHECK=1
		shift
		;;
	--output-dir)
		shift
		OUTPUT_DIR="${1:-}"
		[[ -n "${OUTPUT_DIR}" ]] || {
			echo "error: --output-dir requires a path" >&2
			exit 1
		}
		shift
		;;
	-h | --help)
		cat <<'EOF'
Usage: export-rust-sbom.sh [--check]

Writes sdk/rust/cyt-indexer/cyt-indexer.cdx.json (always when cargo-cyclonedx is available).
Optionally writes cyt-indexer.snyk.json when the Snyk CLI is installed locally.

With --check, fails if committed cyt-indexer.cdx.json differs from a fresh export.
Snyk Cloud handles vulnerability scanning; cyt-indexer.snyk.json is not checked in CI.

With --output-dir DIR, writes generated files under DIR instead of the repo.
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
		.displayTargetFile = "sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
		| .targetFile = "sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
		| .path = "sdk/rust/cyt-indexer"
		| walk(
			if type == "string" then
				gsub("^" + $root + "/"; "")
				| gsub("^" + $root + "$"; ".")
			else
				.
			end
		)
	'
}

install_if_changed() {
	local src="$1"
	local dst="$2"

	if [[ -f "${dst}" ]] && cmp -s "${src}" "${dst}"; then
		return 0
	fi
	cp "${src}" "${dst}"
}

export_cdx_sbom() {
	local cdx_out="$1"
	local cdx_tmp cdx_raw cdx_backup=""

	require_cmd cargo
	require_cmd jq
	[[ -f "${MANIFEST}" ]] || {
		echo "error: missing ${MANIFEST}" >&2
		exit 1
	}

	cdx_tmp="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-cdx.XXXXXX")"
	cdx_raw="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-cdx-raw.XXXXXX")"

	if [[ "${cdx_out}" != "${CDX_FILE}" && -f "${CDX_FILE}" ]]; then
		cdx_backup="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-cdx-backup.XXXXXX")"
		cp "${CDX_FILE}" "${cdx_backup}"
	fi

	chunk_run_in_nopatch_workspace "${REPO_ROOT}" \
		run_cmd cargo cyclonedx --manifest-path sdk/rust/cyt-indexer/Cargo.toml --format json

	[[ -f "${CDX_FILE}" ]] || {
		echo "error: cargo cyclonedx did not write ${CDX_FILE}" >&2
		exit 1
	}

	cp "${CDX_FILE}" "${cdx_raw}"
	sanitize_cdx "${cdx_raw}" "${REPO_ROOT}" | jq '.' >"${cdx_tmp}"
	assert_no_absolute_paths "${cdx_tmp}" "cyt-indexer.cdx.json"
	install_if_changed "${cdx_tmp}" "${cdx_out}"

	if [[ -n "${cdx_backup}" ]]; then
		cp "${cdx_backup}" "${CDX_FILE}"
	fi

	rm -f "${cdx_tmp}" "${cdx_raw}" "${cdx_backup}"
}

export_snyk_sbom_if_available() {
	local cdx_for_snyk="$1"
	local snyk_out="$2"
	local snyk_tmp snyk_status snyk_backup=""

	if ! command -v snyk >/dev/null 2>&1; then
		echo "note: snyk CLI not installed; skipping cyt-indexer.snyk.json (Snyk Cloud scans the repo)" >&2
		return 0
	fi

	require_cmd jq
	[[ -f "${cdx_for_snyk}" ]] || {
		echo "error: missing ${cdx_for_snyk} for snyk export" >&2
		exit 1
	}

	snyk_tmp="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-snyk.XXXXXX")"

	if [[ "${snyk_out}" != "${SNYK_FILE}" && -f "${SNYK_FILE}" ]]; then
		snyk_backup="$(mktemp "${TMPDIR:-/tmp}/export-rust-sbom-snyk-backup.XXXXXX")"
		cp "${SNYK_FILE}" "${snyk_backup}"
	fi

	(
		cd "${REPO_ROOT}"
		run_cmd snyk sbom test \
			--file="${cdx_for_snyk}" \
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

	jq -S '.' "${snyk_tmp}" | sanitize_snyk "${REPO_ROOT}" >"${snyk_tmp}.sorted"
	assert_no_absolute_paths "${snyk_tmp}.sorted" "cyt-indexer.snyk.json"
	install_if_changed "${snyk_tmp}.sorted" "${snyk_out}"

	if [[ -n "${snyk_backup}" ]]; then
		cp "${snyk_backup}" "${SNYK_FILE}"
	fi

	rm -f "${snyk_tmp}" "${snyk_tmp}.sorted" "${snyk_backup}"
}

export_rust_sbom() {
	local cdx_out="$1"
	local snyk_out="$2"

	export_cdx_sbom "${cdx_out}"
	export_snyk_sbom_if_available "${cdx_out}" "${snyk_out}"
}

files_match() {
	local expected="$1"
	local actual="$2"
	diff -q "${expected}" "${actual}" >/dev/null 2>&1
}

if [[ "${DO_CHECK}" -eq 1 ]]; then
	tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/export-rust-sbom.XXXXXX")"
	trap 'rm -rf "${tmp_dir}"' EXIT
	export_cdx_sbom "${tmp_dir}/cyt-indexer.cdx.json"

	status=0
	committed="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
	generated="${tmp_dir}/cyt-indexer.cdx.json"
	if [[ ! -f "${committed}" ]]; then
		echo "error: missing ${committed} (run: ./scripts/deps/export-rust-sbom.sh)" >&2
		status=1
	elif ! files_match "${committed}" "${generated}"; then
		echo "error: sdk/rust/cyt-indexer/cyt-indexer.cdx.json is out of sync (run: ./scripts/deps/export-rust-sbom.sh)" >&2
		status=1
	fi
	exit "${status}"
fi

if [[ -n "${OUTPUT_DIR}" ]]; then
	mkdir -p "${OUTPUT_DIR}"
	export_rust_sbom "${OUTPUT_DIR}/cyt-indexer.cdx.json" "${OUTPUT_DIR}/cyt-indexer.snyk.json"
	exit 0
fi

export_rust_sbom "${CDX_FILE}" "${SNYK_FILE}"
