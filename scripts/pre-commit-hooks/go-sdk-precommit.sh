#!/usr/bin/env bash
# Run Go SDK pre-commit tools scoped to sdk/go.
set -euo pipefail

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
GO_DIR="${ROOT}/sdk/go"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=scripts/pre-commit-hooks/go-sdk-tools.sh
source "${SCRIPT_DIR}/go-sdk-tools.sh"

cd "$GO_DIR"
export CGO_ENABLED=1
host_triplet="$(rustc -vV | sed -n 's/^host: //p')"
go_tools_bin_dir="$(go_sdk_tools_bin_dir)"
export PATH="${ROOT}/target/${host_triplet}/release:${go_tools_bin_dir}:${PATH}"

windows_llvm_mingw_root() {
	if [[ -n "${LLVM_MINGW_ROOT:-}" && -d "${LLVM_MINGW_ROOT}/bin" ]]; then
		printf '%s\n' "${LLVM_MINGW_ROOT}"
		return 0
	fi
	local candidate="${HOME}/tools/llvm-mingw"
	if [[ -d "${candidate}/bin" ]]; then
		printf '%s\n' "${candidate}"
		return 0
	fi
	return 1
}

configure_windows_go_cgo() {
	case "$(uname -s 2>/dev/null || echo unknown)" in
	MINGW* | MSYS* | CYGWIN*)
		local gcc="" mingw_prefix="" mingw_root="" lib_dir=""
		if mingw_root="$(windows_llvm_mingw_root)"; then
			export PATH="${mingw_root}/bin:${PATH}"
		fi
		case "${host_triplet}" in
		aarch64-pc-windows-msvc)
			gcc=aarch64-w64-mingw32-gcc
			mingw_prefix=aarch64-w64-mingw32
			;;
		x86_64-pc-windows-msvc)
			gcc=x86_64-w64-mingw32-gcc
			mingw_prefix=x86_64-w64-mingw32
			;;
		esac
		if [[ -n "$gcc" ]] && command -v "$gcc" >/dev/null 2>&1; then
			export CC="$gcc"
			export CXX="${gcc%-gcc}-g++"
			if [[ -n "$mingw_root" && -n "$mingw_prefix" && -d "${mingw_root}/${mingw_prefix}/lib" ]]; then
				lib_dir="${mingw_root}/${mingw_prefix}/lib"
				export CGO_LDFLAGS="${CGO_LDFLAGS:-} -L${lib_dir}"
				export LIBRARY_PATH="${lib_dir}${LIBRARY_PATH:+:${LIBRARY_PATH}}"
			fi
		fi
		;;
	esac
}

go_native_ensure_args() {
	case "${host_triplet}" in
	*-pc-windows-msvc)
		# Windows cgo uses MinGW; static-only copies MSVC cyt_indexer.lib (incompatible).
		;;
	*)
		printf '%s\n' -static-only
		;;
	esac
}

prepare_windows_go_native() {
	case "${host_triplet}" in
	*-pc-windows-msvc)
		local lib_dir="${ROOT}/target/${host_triplet}/release"
		local native_dir="${GO_DIR}/native/${host_triplet}"
		bash "${GO_DIR}/scripts/prepare-windows-cgo.sh" "$lib_dir" "$native_dir"
		;;
	esac
}

rel_paths() {
	local out=()
	local f
	for f in "$@"; do
		out+=("${f#sdk/go/}")
	done
	printf '%s\n' "${out[@]}"
}

tool=${1:?usage: go-sdk-precommit.sh TOOL [args...]}
shift

case "$tool" in
fumpt)
	mapfile -t files < <(rel_paths "$@")
	if ((${#files[@]})); then
		gofumpt_bin="$(ensure_go_sdk_tool gofumpt "${GO_SDK_TOOL_GOFUMPT}")"
		(
			cd "$GO_DIR"
			"${gofumpt_bin}" -l -w "${files[@]}"
		)
	fi
	;;
imports)
	mapfile -t files < <(rel_paths "$@")
	if ((${#files[@]})); then
		goimports_bin="$(ensure_go_sdk_tool goimports "${GO_SDK_TOOL_GOIMPORTS}")"
		(
			cd "$GO_DIR"
			"${goimports_bin}" -w "${files[@]}"
		)
	fi
	;;
tidy)
	go mod tidy
	;;
staticcheck)
	staticcheck_bin="$(ensure_go_sdk_tool staticcheck "${GO_SDK_TOOL_STATICCHECK}")"
	(
		cd "$GO_DIR"
		"${staticcheck_bin}" ./...
	)
	;;
critic)
	mapfile -t files < <(rel_paths "$@")
	gocritic_bin="$(ensure_go_sdk_tool gocritic "${GO_SDK_TOOL_GOCRITIC}")"
	for f in "${files[@]}"; do
		(
			cd "$GO_DIR"
			"${gocritic_bin}" check "./${f}"
		)
	done
	;;
sec)
	gosec_bin="$(ensure_go_sdk_tool gosec "${GO_SDK_TOOL_GOSEC}")"
	(
		cd "$GO_DIR"
		"${gosec_bin}" ./...
	)
	;;
build)
	configure_windows_go_cgo
	prepare_windows_go_native
	mapfile -t ensure_args < <(go_native_ensure_args)
	go run ./cmd/cyt-native-ensure "${ensure_args[@]}"
	go build ./...
	;;
test)
	configure_windows_go_cgo
	env -u CARGO_TARGET_DIR "${ROOT}/sdk/c/scripts/build-c-lib.sh" --no-sync-header
	prepare_windows_go_native
	mapfile -t ensure_args < <(go_native_ensure_args)
	go run ./cmd/cyt-native-ensure "${ensure_args[@]}"
	env -u CARGO_TARGET_DIR go test ./...
	bash "${ROOT}/scripts/local/dev/heal-cargo-lock.sh"
	;;
*)
	echo "unknown tool: $tool" >&2
	exit 1
	;;
esac
