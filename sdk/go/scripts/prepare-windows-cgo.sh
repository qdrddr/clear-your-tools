#!/usr/bin/env bash
# Build a MinGW-compatible libcyt_indexer.a import library from an MSVC cyt_indexer.dll.
# Go cgo on Windows uses MinGW; Rust CI builds pc-windows-msvc DLLs + import libs.
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: prepare-windows-cgo.sh LIB_DIR [NATIVE_DIR]

Generate libcyt_indexer.a beside cyt_indexer.dll for Go cgo (-lcyt_indexer).
If NATIVE_DIR is set, also copy the .a there for sdk/go/native/<triplet>/.
EOF
}

die() {
	echo "error: $*" >&2
	exit 1
}

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

lib_dir="${1:-}"
native_dir="${2:-}"

if [[ -z "$lib_dir" || "$1" == "-h" || "$1" == "--help" ]]; then
	usage
	exit 0
fi

dll="${lib_dir}/cyt_indexer.dll"
import_a="${lib_dir}/libcyt_indexer.a"

[[ -f "$dll" ]] || die "expected DLL missing: $dll"

if [[ -f "$import_a" ]]; then
	if [[ -n "$native_dir" ]]; then
		mkdir -p "$native_dir"
		cp -f "$import_a" "${native_dir}/libcyt_indexer.a"
	fi
	exit 0
fi

require_cmd gendef
require_cmd dlltool

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

cp "$dll" "${tmpdir}/cyt_indexer.dll"
(
	cd "$tmpdir"
	gendef cyt_indexer.dll
	dlltool --input-def cyt_indexer.def --dllname cyt_indexer.dll --output-lib libcyt_indexer.a
)

cp "${tmpdir}/libcyt_indexer.a" "$import_a"
if [[ -n "$native_dir" ]]; then
	mkdir -p "$native_dir"
	cp -f "$import_a" "${native_dir}/libcyt_indexer.a"
fi

echo "==> prepared MinGW import library: ${import_a}"
