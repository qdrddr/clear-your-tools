#!/usr/bin/env bash
# Pre-commit wrapper for export-rust-sbom.sh.
#
# Exports to a temp directory first so cyclonedx/snyk never leave the committed
# SBOM files in a dirty state when content is already current. When content
# does change, copies the generated files into the repo and stages them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CDX_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.cdx.json"
SNYK_FILE="${REPO_ROOT}/sdk/rust/cyt-indexer/cyt-indexer.snyk.json"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/export-rust-sbom-precommit.XXXXXX")"
trap 'rm -rf "${tmp_dir}"' EXIT

if ! bash "${SCRIPT_DIR}/export-rust-sbom.sh" --output-dir "${tmp_dir}"; then
	exit 1
fi

updated=0
for name in cyt-indexer.cdx.json cyt-indexer.snyk.json; do
	src="${tmp_dir}/${name}"
	dst="${REPO_ROOT}/sdk/rust/cyt-indexer/${name}"
	if [[ ! -f "${src}" ]]; then
		echo "error: missing generated ${name}" >&2
		exit 1
	fi
	if [[ ! -f "${dst}" ]] || ! cmp -s "${src}" "${dst}"; then
		cp "${src}" "${dst}"
		updated=1
	fi
done

if ((updated)); then
	git -C "${REPO_ROOT}" add "${CDX_FILE}" "${SNYK_FILE}" >/dev/null 2>&1 || true
fi

exit 0
