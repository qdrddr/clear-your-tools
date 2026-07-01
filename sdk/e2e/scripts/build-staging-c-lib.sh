#!/usr/bin/env bash
# Build libcyt_indexer in CYT_E2E_STAGING for Go/C E2E harnesses.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="${CYT_E2E_STAGING:?run prepare-release-checkout.sh first}"
TRIPLET="${CYT_RUST_TARGET:-$("${ROOT}/scripts/host-rust-target.sh")}"
PROFILE="${CYT_C_LIB_PROFILE:-release}"

release_flag=(--release)
if [[ "$PROFILE" == "debug" ]]; then
	release_flag=()
fi

if [[ ! -f "${STAGING}/Cargo.toml" ]]; then
	echo "::error::missing Cargo.toml in CYT_E2E_STAGING=${STAGING}" >&2
	exit 1
fi

# cgo in sdk/go links ${SRCDIR}/../../target/<triplet>/<profile>; keep artifacts there.
export CARGO_TARGET_DIR="${STAGING}/target"

rustup target add "$TRIPLET" >/dev/null 2>&1 || true

host="$("${ROOT}/scripts/host-rust-target.sh")"
target_args=(--target "$TRIPLET")
artifact_dir="${CARGO_TARGET_DIR}/${TRIPLET}/${PROFILE}"
if [[ "$TRIPLET" == "$host" ]]; then
	# Host builds also populate target/<profile>/; cgo expects target/<triplet>/<profile>/.
	target_args=(--target "$TRIPLET")
fi

echo "Building cyt-indexer ffi in ${STAGING} for ${TRIPLET}/${PROFILE}" >&2
(
	cd "$STAGING"
	cargo clean -p cyt-indexer --target "$TRIPLET" >/dev/null 2>&1 || true
	cargo build -p cyt-indexer --no-default-features --features ffi \
		"${target_args[@]}" "${release_flag[@]}"
)

if [[ "$TRIPLET" == "$host" && ! -f "${artifact_dir}/libcyt_indexer.dylib" && ! -f "${artifact_dir}/libcyt_indexer.so" && ! -f "${artifact_dir}/cyt_indexer.dll" ]]; then
	host_dir="${CARGO_TARGET_DIR}/${PROFILE}"
	mkdir -p "$artifact_dir"
	shopt -s nullglob
	for artifact in "${host_dir}/libcyt_indexer.dylib" "${host_dir}/libcyt_indexer.so" \
		"${host_dir}/cyt_indexer.dll" "${host_dir}/cyt_indexer.dll.lib" \
		"${host_dir}/libcyt_indexer.a" "${host_dir}/cyt_indexer.lib"; do
		if [[ -f "$artifact" ]]; then
			cp -f "$artifact" "${artifact_dir}/$(basename "$artifact")"
		fi
	done
	shopt -u nullglob
fi

header_src="${STAGING}/sdk/rust/cyt-indexer/cyt_indexer.h"
header_dst="${STAGING}/sdk/c/include/cyt_indexer.h"
[[ -f "$header_src" ]] || {
	echo "::error::missing generated header: ${header_src}" >&2
	exit 1
}
mkdir -p "$(dirname "$header_dst")"
cp "$header_src" "$header_dst"
echo "Synced header -> ${header_dst}" >&2

shared=""
case "${TRIPLET}" in
*-pc-windows-msvc) shared="${artifact_dir}/cyt_indexer.dll" ;;
*-apple-darwin) shared="${artifact_dir}/libcyt_indexer.dylib" ;;
*) shared="${artifact_dir}/libcyt_indexer.so" ;;
esac
[[ -f "$shared" ]] || {
	echo "::error::missing shared library: ${shared}" >&2
	exit 1
}
