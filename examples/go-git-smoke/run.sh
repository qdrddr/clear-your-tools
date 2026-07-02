#!/usr/bin/env bash
# Build and run the git-tag smoke test outside the monorepo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

export CGO_ENABLED=1
export CYT_RELEASE_VERSION="${CYT_RELEASE_VERSION:-0.6.4}"

STAGING="$("${ROOT}/prepare.sh")"
"${ROOT}/ensure-ffi.sh" "$STAGING" "$CYT_RELEASE_VERSION"
eval "$("${ROOT}/ensure-ffi.sh" --print-cgo "$STAGING")"

go mod tidy
go build -o ./cyt-go-git-smoke .
./cyt-go-git-smoke
