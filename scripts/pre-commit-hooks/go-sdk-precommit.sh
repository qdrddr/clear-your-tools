#!/usr/bin/env bash
# Run Go SDK pre-commit tools scoped to sdk/go.
set -euo pipefail

ROOT="$(cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" && pwd -P)"
GO_DIR="${ROOT}/sdk/go"

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
		go tool gofumpt -l -w "${files[@]}"
	fi
	;;
imports)
	mapfile -t files < <(rel_paths "$@")
	if ((${#files[@]})); then
		go tool goimports -w "${files[@]}"
	fi
	;;
tidy)
	go mod tidy
	;;
staticcheck)
	go tool staticcheck ./...
	;;
critic)
	mapfile -t files < <(rel_paths "$@")
	for f in "${files[@]}"; do
		go tool gocritic check "./${f}"
	done
	;;
sec)
	go tool gosec ./...
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
