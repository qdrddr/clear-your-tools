#!/usr/bin/env bash
# Download C FFI artifacts from GitHub Release for Go/C E2E harnesses.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="${CYT_E2E_STAGING:?run prepare-release-checkout.sh first}"
VERSION="${CYT_RELEASE_VERSION:?set CYT_RELEASE_VERSION}"
TRIPLET="${CYT_RUST_TARGET:-$("${ROOT}/scripts/host-rust-target.sh")}"

GO_SDK="${STAGING}/sdk/go"
if [[ ! -f "${GO_SDK}/go.mod" ]]; then
	echo "::error::missing Go SDK in staging: ${GO_SDK}/go.mod" >&2
	exit 1
fi

echo "Ensuring release native artifacts for ${VERSION} (${TRIPLET})" >&2
(
	cd "${GO_SDK}"
	CYT_RELEASE_VERSION="${VERSION}" go run ./cmd/cyt-native-ensure -version "${VERSION}"
)

native_dir="${GO_SDK}/native/${TRIPLET}"
target_dir="${STAGING}/target/${TRIPLET}/release"
mkdir -p "${target_dir}"

shopt -s nullglob
for artifact in "${native_dir}/"*; do
	[[ -f "$artifact" ]] || continue
	cp -f "$artifact" "${target_dir}/$(basename "$artifact")"
done
shopt -u nullglob

shared=""
case "${TRIPLET}" in
*-pc-windows-msvc) shared="${target_dir}/cyt_indexer.dll" ;;
*-apple-darwin) shared="${target_dir}/libcyt_indexer.dylib" ;;
*) shared="${target_dir}/libcyt_indexer.so" ;;
esac
[[ -f "$shared" ]] || {
	echo "::error::missing shared library after ensure: ${shared}" >&2
	exit 1
}

header_dst="${STAGING}/sdk/c/include/cyt_indexer.h"
if [[ -f "${native_dir}/cyt_indexer.h" ]]; then
	mkdir -p "$(dirname "$header_dst")"
	cp -f "${native_dir}/cyt_indexer.h" "$header_dst"
fi

echo "Release native ready: ${native_dir} -> ${target_dir}" >&2
