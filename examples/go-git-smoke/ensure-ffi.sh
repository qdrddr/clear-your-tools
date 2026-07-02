#!/usr/bin/env bash
# Optional helper: run sdk/go's cyt-native-ensure against a sparse-cloned release checkout.
set -euo pipefail

host_triplet() {
	case "$(uname -s)/$(uname -m)" in
	Darwin/arm64) echo "aarch64-apple-darwin" ;;
	Darwin/x86_64) echo "x86_64-apple-darwin" ;;
	Linux/x86_64) echo "x86_64-unknown-linux-gnu" ;;
	Linux/aarch64 | Linux/arm64) echo "aarch64-unknown-linux-gnu" ;;
	MINGW*/x86_64 | MSYS*/x86_64) echo "x86_64-pc-windows-msvc" ;;
	MINGW*/aarch64 | MSYS*/aarch64) echo "aarch64-pc-windows-msvc" ;;
	*)
		echo "unsupported platform: $(uname -s)/$(uname -m)" >&2
		exit 1
		;;
	esac
}

ensure_ffi() {
	local staging="$1"
	local version="${2#v}"
	local triplet
	local native_dir

	triplet="$(host_triplet)"
	native_dir="${staging}/sdk/go/native/${triplet}"

	echo "Running sdk/go cyt-native-ensure..." >&2
	(
		cd "${staging}/sdk/go"
		CYT_RELEASE_VERSION="${version}" go run ./cmd/cyt-native-ensure \
			-version "${version}" \
			-native-dir "${native_dir}" \
			-static-only \
			-force
	)
}

print_cgo_env() {
	local staging="$1"
	(
		cd "${staging}/sdk/go"
		go run ./cmd/cyt-native-ensure --print-env
	)
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	case "${1:-}" in
	--print-cgo)
		print_cgo_env "${2:?staging dir}"
		;;
	*)
		ensure_ffi "${1:?staging dir}" "${2:?version}"
		;;
	esac
fi
