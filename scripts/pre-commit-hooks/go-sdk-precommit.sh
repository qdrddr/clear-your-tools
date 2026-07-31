#!/usr/bin/env bash
# Run Go SDK pre-commit tools scoped to sdk/go.
set -euo pipefail

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
GO_DIR="${ROOT}/sdk/go"
TOOLS_DIR="${GO_DIR}/tools"

cd "$GO_DIR"
export CGO_ENABLED=1
host_triplet="$(rustc -vV | sed -n 's/^host: //p')"
export PATH="${ROOT}/target/${host_triplet}/release:${PATH}"

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
		gofumpt_bin="$(go -C "${TOOLS_DIR}" tool -n gofumpt)"
		(
			cd "$GO_DIR"
			"${gofumpt_bin}" -l -w "${files[@]}"
		)
	fi
	;;
imports)
	mapfile -t files < <(rel_paths "$@")
	if ((${#files[@]})); then
		goimports_bin="$(go -C "${TOOLS_DIR}" tool -n goimports)"
		(
			cd "$GO_DIR"
			"${goimports_bin}" -w "${files[@]}"
		)
	fi
	;;
tidy)
	go mod tidy
	go -C "${TOOLS_DIR}" mod tidy
	;;
staticcheck)
	staticcheck_bin="$(go -C "${TOOLS_DIR}" tool -n staticcheck)"
	(
		cd "$GO_DIR"
		"${staticcheck_bin}" ./...
	)
	;;
critic)
	mapfile -t files < <(rel_paths "$@")
	for f in "${files[@]}"; do
		(
			cd "$GO_DIR"
			go -C "${TOOLS_DIR}" tool gocritic check "./${f}"
		)
	done
	;;
sec)
	gosec_bin="$(go -C "${TOOLS_DIR}" tool -n gosec)"
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
