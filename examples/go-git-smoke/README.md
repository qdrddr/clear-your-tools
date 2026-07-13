# Go SDK git smoke test

Minimal app that consumes `github.com/qdrddr/clear-your-tools/sdk/go` from git tag **v0.6.4** and checks that:

1. A sparse clone of the release tag provides the Go SDK sources
2. C FFI artifacts can be fetched from the matching GitHub Release
3. The binary links and runs with CGO outside the monorepo

## Run anywhere

Copy this folder to any directory, then:

```bash
cd go-git-smoke
chmod +x prepare.sh ensure-ffi.sh run.sh
./run.sh
```

## How it works

The repo publishes release tag `v0.6.4` at the root, not `sdk/go/v0.6.4`, so plain
`go get github.com/qdrddr/clear-your-tools/sdk/go@v0.6.4` does not resolve today.
This smoke test mirrors the registry E2E harness:

1. `prepare.sh` sparse-clones `v0.6.4` into `.staging/0.6.4/`
2. Renders `go.mod` from `go.mod.in` with `replace => .staging/.../sdk/go`
3. `ensure-ffi.sh` (optional) delegates to `sdk/go/cmd/cyt-native-ensure`,
which downloads `cyt-indexer-ffi-<triplet>.tar.gz` from GitHub Releases
4. `run.sh` links the static archive via `cyt-native-ensure --print-env` and runs a tiny API call

Release `.dylib` / `.so` files embed CI rpaths; `cyt-native-ensure -static-only` installs only `libcyt_indexer.a`
(or `.lib` on Windows) to avoid runtime load failures on macOS.

## Manual steps

```bash
export CGO_ENABLED=1
export CYT_RELEASE_VERSION=0.6.4

./prepare.sh
STAGING="$(./prepare.sh)"
./ensure-ffi.sh "$STAGING" "$CYT_RELEASE_VERSION"
eval "$(./ensure-ffi.sh --print-cgo "$STAGING")"

go mod tidy
go build -o cyt-go-git-smoke .
./cyt-go-git-smoke
```

Override clone location or remote:

```bash
export CYT_GIT_STAGING=/tmp/my-cyt-checkout
export CYT_GIT_REPO=https://github.com/qdrddr/clear-your-tools.git
./run.sh
```

## Prerequisites

- Go 1.25+ with CGO enabled
- git, curl, and network access for clone + GitHub Release download
- C toolchain (clang/gcc; Xcode CLT on macOS)

## Expected output

```text
cyt-indexer Go git smoke OK
  sdk module version: 0.6.4
  native lib version: 0.6.4
  empty catalog index bytes: 40
  cwd: /path/to/your/copy
```
