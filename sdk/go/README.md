# cyt-indexer Go SDK

Go bindings for [cyt-indexer](../rust/cyt-indexer/) via **cgo**, wrapping the same C library and header as
the [C SDK](../c/README.md).

```text
GitHub Release / build-c-lib.sh  →  libcyt_indexer + cyt_indexer.h
                                           ↓                    ↓
                                       sdk/c (C apps)      sdk/go (cgo)
```

## Prerequisites

- Go 1.24+ with **CGO enabled** (`CGO_ENABLED=1`)
- C toolchain (same as sdk/c)
- Native C library for your platform (see **C native artifacts** below)

## Quick start

```bash
cd sdk/go
go tool cyt-native-ensure    # downloads prebuilt C FFI for your platform (once per version)
CGO_ENABLED=1 go test ./...
```

```bash
go get github.com/qdrddr/clear-your-tools/sdk/go
```

```go
import cytindexer "github.com/qdrddr/clear-your-tools/sdk/go"

indexJSON, err := cytindexer.BuildCatalogIndex(`[]`, `[]`)
if err != nil {
    log.Fatal(err)
}
```

---

## C native artifacts

The Go module is source-only on `go get`. The Rust/C implementation ships as **prebuilt C FFI artifacts**
attached to each [GitHub Release](https://github.com/qdrddr/clear-your-tools/releases) (published by
`1. Publish C FFI artifacts to GitHub Release` in CI).

### Automatic (recommended)

From your project directory (or this repo's `sdk/go`):

```bash
go tool cyt-native-ensure
# or: go generate ./...   (runs the same via //go:generate in cgo_lib.go)
CGO_ENABLED=1 go build ./...
```

When the module cache is read-only (typical after `go get`), export linker flags from the cache:

```bash
eval "$(go tool cyt-native-ensure --print-env)"
CGO_ENABLED=1 go build ./...
```

`cyt-native-ensure`:

1. Reuses `target/<triplet>/release/` when developing in this monorepo (after `build-c-lib.sh`)
2. Otherwise downloads `cyt-indexer-ffi-<triplet>.tar.gz` from GitHub Release matching the SDK version
3. Verifies `SHA256SUMS` from the same release
4. Installs into `$XDG_CACHE_HOME/cyt-indexer/<version>/<triplet>/` and copies into `sdk/go/native/<triplet>/` when writable

### Manual download from GitHub Release

For version `v0.6.1`, assets are at:

`https://github.com/qdrddr/clear-your-tools/releases/download/v0.6.1/`

| Rust triplet | Archive |
| --- | --- |
| `x86_64-unknown-linux-gnu` | `cyt-indexer-ffi-x86_64-unknown-linux-gnu.tar.gz` |
| `aarch64-unknown-linux-gnu` | `cyt-indexer-ffi-aarch64-unknown-linux-gnu.tar.gz` |
| `x86_64-apple-darwin` | `cyt-indexer-ffi-x86_64-apple-darwin.tar.gz` |
| `aarch64-apple-darwin` | `cyt-indexer-ffi-aarch64-apple-darwin.tar.gz` |
| `x86_64-pc-windows-msvc` | `cyt-indexer-ffi-x86_64-pc-windows-msvc.tar.gz` |
| `aarch64-pc-windows-msvc` | `cyt-indexer-ffi-aarch64-pc-windows-msvc.tar.gz` |

Each archive contains:

| File | Purpose |
| --- | --- |
| `libcyt_indexer.so` / `.dylib` / `cyt_indexer.dll` | Shared library (C SDK) |
| `libcyt_indexer.a` / `cyt_indexer.lib` | Static library (Go links via `-lcyt_indexer` from `native/`) |
| `cyt_indexer.h` | C header (also published standalone on the release) |
| `cyt_indexer.dll.lib` | Windows import library (when applicable) |

Extract into `sdk/go/native/<triplet>/` or set `CYT_NATIVE_DIR` and use `--print-env`.

Verify with `SHA256SUMS` on the release page.

### Manual build from source

Requires Rust and the repo checkout:

```bash
./sdk/c/scripts/build-c-lib.sh --target $(rustc -vV | sed -n 's/^host: //p')
cd sdk/go && go run ./cmd/cyt-native-ensure
CGO_ENABLED=1 go test ./...
```

The Go package searches, in order:

1. `sdk/go/native/<triplet>/` (populated by `cyt-native-ensure`)
2. `target/<triplet>/release/` (monorepo layout after `build-c-lib.sh`)
3. `CGO_LDFLAGS` / `CGO_CFLAGS` from `--print-env` (external consumers)

### Environment variables

| Variable | Description |
| --- | --- |
| `CGO_ENABLED` | Must be `1` (default off on some platforms) |
| `CYT_RELEASE_VERSION` | Semver for release download (overrides embedded default) |
| `CYT_NATIVE_DIR` | Root directory for cached natives (instead of XDG cache) |
| `CGO_LDFLAGS` / `CGO_CFLAGS` | Set via `eval "$(go tool cyt-native-ensure --print-env)"` when needed |

`cyt-native-ensure` flags:

| Flag | Description |
| --- | --- |
| `-static-only` | Install only static library + header (recommended on macOS; release dylibs embed CI rpaths) |
| `-native-dir` | Copy artifacts into a writable directory (default: `sdk/go/native/<triplet>/` when writable) |
| `-force` | Re-download even if cached artifacts exist |

### Static vs shared linking

Go prefers **static** `libcyt_indexer.a` when present in the `-L` search path (`native/` or cache).
The linker falls back to the shared library in `target/<triplet>/release/` for monorepo development.
C SDK consumers typically use the shared library from the same release archives.

### Troubleshooting

| Problem | Fix |
| --- | --- |
| `cannot find -lcyt_indexer` | Run `go tool cyt-native-ensure` or build with `build-c-lib.sh` |
| Release download 404 | Ensure the GitHub Release exists for your SDK version; C FFI publish runs after crates.io |
| Checksum error | Re-download; confirm `SHA256SUMS` matches the release |
| `CGO_ENABLED=0` | Export `CGO_ENABLED=1` |
| Windows link errors | Use MSVC toolchain; ensure `cyt_indexer.lib` is in `native/<triplet>/` |

### Monorepo developers

If you already ran `./sdk/c/scripts/build-c-lib.sh`, `cyt-native-ensure` copies from `target/` into
`native/` and **no download** is needed.

---

## Module layout

| File | Wraps |
| ---- | ----- |
| `build.go` | Catalog build, Anthropic helpers, `CatalogBuilder` |
| `catalog_io.go` | `WriteCatalogIndex` |
| `retrieve.go` | `DecomposedCatalog`, `RetrieveTools`, `LoadCatalog` |
| `paths.go` | Path constants, `CollectEnums` |
| `runtime.go` | Runtime scoring defaults |
| `policies.go` | Full policies surface |
| `documents.go` | Document extraction |
| `bm25.go` | BM25 cohesion |
| `pageindex.go` | Skills builder, pageindex |
| `cgo_lib.go` | Internal cgo bridge (not imported by consumers) |
| `cmd/cyt-native-ensure/` | Downloads or copies C FFI artifacts |

All public functions delegate to `cyt_*` via cgo — no duplicate Rust logic in Go.

## Testing

```bash
cd sdk/go
go tool cyt-native-ensure
CGO_ENABLED=1 go test ./...
```

Parity tests (`parity_test.go`) compare Go output against the Python `_native` module when `uv sync` has been run at
the repo root. Skip with `CYT_SKIP_PARITY=1`.

## Thread safety

Same as the C API: JSON in/out at the FFI boundary; error messages are thread-local. Global config functions
(`ConfigureRuntimeDefaults`, `ConfigurePathConstants`) match Python/TypeScript semantics.

## Related SDKs

- [C SDK](../c/README.md) — shared build step, header, and GitHub Release artifacts
- [Rust SDK](../rust/README.md)
- [Python SDK](../python/README.md)
- [TypeScript SDK](../typescript/)
