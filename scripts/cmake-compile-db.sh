#!/usr/bin/env bash
# Generate sdk/c/compile_commands.json for clang-tidy (prek cmake-compile-db hook).
set -euo pipefail

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
cd "${ROOT}"

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "error: $1 not found" >&2
		exit 1
	}
}

require_cmd cmake
require_cmd rustc

BUILD_DIR="${ROOT}/sdk/c/build"
mkdir -p "${BUILD_DIR}"

TRIPLET="$(rustc -vV | sed -n 's/^host: //p')"
[[ -n ${TRIPLET} ]] || {
	echo "error: could not detect Rust host triplet from rustc -vV" >&2
	exit 1
}

exec cmake -S "${ROOT}/sdk/c" -B "${BUILD_DIR}" \
	-DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
	-DCMAKE_BUILD_TYPE=Release \
	-DCYT_RUST_TARGET="${TRIPLET}"
