#!/usr/bin/env bash
# shellcheck shell=bash
# Build libcyt_indexer for sdk/c and sdk/go (manual wrapper; also used by CMake and CI).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CRATE_DIR="${REPO_ROOT}/sdk/rust/cyt-indexer"
INCLUDE_DIR="${REPO_ROOT}/sdk/c/include"

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/shorten-paths.sh"
export SHORTEN_ROOT="${REPO_ROOT}"

SUPPORTED_TARGETS=(
	x86_64-unknown-linux-gnu
	aarch64-unknown-linux-gnu
	x86_64-apple-darwin
	aarch64-apple-darwin
	x86_64-pc-windows-msvc
	aarch64-pc-windows-msvc
)

PROFILE="${CYT_C_LIB_PROFILE:-release}"
SYNC_HEADER=1
PRINT_ONLY=0
BUILD_ALL=0
TARGET=""

usage() {
	cat <<'EOF'
Usage: build-c-lib.sh [OPTIONS]

Build the cyt-indexer C shared library (ffi feature) for sdk/c and sdk/go.

Options:
  --target TRIPLET     Rust target triplet (default: host)
  --all                Build all six supported triplets
  --release            Release profile (default)
  --debug              Debug profile
  --sync-header        Copy cyt_indexer.h to sdk/c/include (default)
  --no-sync-header     Skip header copy
  --print-artifacts    Print artifact paths and exit
  -h, --help           Show this help

Environment:
  CARGO_TARGET_DIR     Override Cargo output directory
  CYT_C_LIB_PROFILE    release or debug (alternative to flags)
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

info() {
	echo "==> $*"
}

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

host_target() {
	rustc -vV | sed -n 's/^host: //p'
}

is_supported_target() {
	local t="$1"
	local x
	for x in "${SUPPORTED_TARGETS[@]}"; do
		[[ "$x" == "$t" ]] && return 0
	done
	return 1
}

cargo_target_dir() {
	if [[ -n "${CARGO_TARGET_DIR:-}" ]]; then
		echo "${CARGO_TARGET_DIR}"
	else
		echo "${REPO_ROOT}/target"
	fi
}

artifact_paths() {
	local triplet="$1"
	local prof="$2"
	local base
	base="$(cargo_target_dir)/${triplet}/${prof}"

	case "${triplet}" in
	*-pc-windows-msvc)
		echo "${base}/cyt_indexer.dll"
		echo "${base}/cyt_indexer.dll.lib"
		;;
	*-apple-darwin)
		echo "${base}/libcyt_indexer.dylib"
		;;
	*)
		echo "${base}/libcyt_indexer.so"
		;;
	esac
}

sync_header() {
	local src="${CRATE_DIR}/cyt_indexer.h"
	[[ -f "$src" ]] || die "header not found (build ffi crate first): $src"
	mkdir -p "${INCLUDE_DIR}"
	cp "$src" "${INCLUDE_DIR}/cyt_indexer.h"
	info "synced header -> ${INCLUDE_DIR}/cyt_indexer.h"
}

build_one() {
	local triplet="$1"
	local prof="$2"

	is_supported_target "$triplet" || die "unsupported target: $triplet (see --help)"

	local release_flag=()
	if [[ "$prof" == "release" ]]; then
		release_flag=(--release)
	fi

	info "rustup target add ${triplet} (if needed)"
	rustup target add "$triplet" >/dev/null 2>&1 || true

	info "rtk cargo build -p cyt-indexer --no-default-features --features ffi --target ${triplet} (${prof})"
	(
		cd "${REPO_ROOT}" || die "cd failed"
		rtk cargo build -p cyt-indexer --no-default-features --features ffi \
			--target "$triplet" "${release_flag[@]}"
	)

	if [[ "$SYNC_HEADER" -eq 1 ]]; then
		sync_header
	fi

	info "artifacts for ${triplet}/${prof}:"
	local path
	while IFS= read -r path; do
		[[ -f "$path" ]] || die "expected artifact missing: $path"
		echo "  $path"
	done < <(artifact_paths "$triplet" "$prof")
}

_cyt_build_c_lib_main() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
		--target)
			[[ $# -ge 2 ]] || die "missing value for --target"
			TARGET="$2"
			shift 2
			;;
		--all)
			BUILD_ALL=1
			shift
			;;
		--release)
			PROFILE=release
			shift
			;;
		--debug)
			PROFILE=debug
			shift
			;;
		--sync-header)
			SYNC_HEADER=1
			shift
			;;
		--no-sync-header)
			SYNC_HEADER=0
			shift
			;;
		--print-artifacts)
			PRINT_ONLY=1
			shift
			;;
		-h | --help)
			usage
			return 0
			;;
		*)
			die "unknown option: $1 (try --help)"
			;;
		esac
	done

	require_cmd cargo
	require_cmd rtk
	require_cmd rustup
	[[ -f "${REPO_ROOT}/Cargo.toml" ]] || die "not repo root: ${REPO_ROOT}"
	[[ -f "${CRATE_DIR}/Cargo.toml" ]] || die "missing cyt-indexer crate"

	if [[ "$BUILD_ALL" -eq 1 && -n "$TARGET" ]]; then
		die "use either --all or --target, not both"
	fi

	if [[ -z "$TARGET" ]]; then
		TARGET="$(host_target)"
	fi

	if [[ "$PRINT_ONLY" -eq 1 ]]; then
		artifact_paths "$TARGET" "$PROFILE"
		echo "${INCLUDE_DIR}/cyt_indexer.h"
		return 0
	fi

	if [[ "$BUILD_ALL" -eq 1 ]]; then
		for t in "${SUPPORTED_TARGETS[@]}"; do
			build_one "$t" "$PROFILE"
		done
	else
		build_one "$TARGET" "$PROFILE"
	fi
}

_cyt_build_c_lib_main "$@" 2>&1 | shorten_paths
exit "${PIPESTATUS[0]}"
