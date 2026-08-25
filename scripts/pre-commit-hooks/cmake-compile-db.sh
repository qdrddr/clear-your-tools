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
TRIPLET="$(rustc -vV | sed -n 's/^host: //p')"
[[ -n ${TRIPLET} ]] || {
	echo "error: could not detect Rust host triplet from rustc -vV" >&2
	exit 1
}

cmake_generator_args=()
cmake_compiler_args=()
case "${TRIPLET}" in
*-windows-*)
	# Visual Studio generators do not emit compile_commands.json; Ninja does.
	require_cmd ninja
	cmake_generator_args=(-G Ninja)
	if command -v clang >/dev/null 2>&1; then
		cmake_compiler_args=(-DCMAKE_C_COMPILER=clang)
	fi
	;;
esac

configure_compile_db() {
	rm -rf "${BUILD_DIR}"
	mkdir -p "${BUILD_DIR}"
	cmake -S "${ROOT}/sdk/c" -B "${BUILD_DIR}" \
		"${cmake_generator_args[@]}" \
		"${cmake_compiler_args[@]}" \
		-DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
		-DCMAKE_BUILD_TYPE=Release \
		-DCYT_RUST_TARGET="${TRIPLET}" \
		-DCYT_INSTALL=OFF
}

if ! configure_compile_db; then
	echo "cmake configure failed; retrying with a clean build directory" >&2
	configure_compile_db
fi

[[ -f "${BUILD_DIR}/compile_commands.json" ]] || {
	echo "error: ${BUILD_DIR}/compile_commands.json was not generated" >&2
	exit 1
}
