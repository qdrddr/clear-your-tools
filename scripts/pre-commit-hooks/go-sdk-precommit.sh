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
	go build ./...
	;;
test)
	env -u CARGO_TARGET_DIR "${ROOT}/sdk/c/scripts/build-c-lib.sh" --no-sync-header
	go run ./cmd/cyt-native-ensure -static-only
	env -u CARGO_TARGET_DIR go test ./...
	;;
*)
	echo "unknown tool: $tool" >&2
	exit 1
	;;
esac
