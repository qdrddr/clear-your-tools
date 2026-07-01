# cyt-indexer Go SDK

Go bindings for [cyt-indexer](../rust/cyt-indexer/) via **cgo**, wrapping the same shared C library and header as
the [C SDK](../c/README.md).

```text
build-c-lib.sh  →  libcyt_indexer + cyt_indexer.h
                        ↓                    ↓
                    sdk/c (C apps)      sdk/go (cgo)
```

## Prerequisites

- Go 1.24+ with **CGO enabled** (`CGO_ENABLED=1`)
- C toolchain (same as sdk/c)
- Shared C library built for your target triplet

## Build the C library first

```bash
./sdk/c/scripts/build-c-lib.sh
```

The Go package links against `target/<triplet>/release/` via `#cgo LDFLAGS` in `cgo_lib.go`.

## Install / use

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

All public functions delegate to `cyt_*` via cgo — no duplicate Rust logic in Go.

## Testing

```bash
cd sdk/go
CGO_ENABLED=1 go test ./...
```

Parity tests (`parity_test.go`) compare Go output against the Python `_native` module when `uv sync` has been run at
the repo root. Skip with `CYT_SKIP_PARITY=1`.

## Thread safety

Same as the C API: JSON in/out at the FFI boundary; error messages are thread-local. Global config functions
(`ConfigureRuntimeDefaults`, `ConfigurePathConstants`) match Python/TypeScript semantics.

## Related SDKs

- [C SDK](../c/README.md) — shared build step and header
- [Go SDK](../go/README.md)
- [Rust SDK](../rust/README.md)
- [Python SDK](../python/README.md)
- [TypeScript SDK](../typescript/)
