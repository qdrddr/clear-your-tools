# cyt-indexer C SDK

Pure C integration for [cyt-indexer](../rust/cyt-indexer/) — tool schema decomposition and catalog indexing for
MCP tool gating.

This package links the shared C library (`libcyt_indexer` / `cyt_indexer.dll`) generated from the Rust crate's `ffi` feature.

## Prerequisites

- C11 compiler (GCC, Clang, or MSVC)
- CMake 3.16+ (recommended)
- Rust toolchain (`cargo`, `rustup`) — only when [building from source](#build-from-source)

## Prebuilt binaries (GitHub Release)

Precompiled `libcyt_indexer` libraries for Linux, macOS, and Windows (x86_64 and ARM64) are attached to
each [GitHub Release](https://github.com/qdrddr/clear-your-tools/releases).

<details>
<summary><strong>Assets are at</strong></summary>

`https://github.com/qdrddr/clear-your-tools/releases/download/v0.6.9/`

Example (macOS ARM64):

```bash
VERSION=v0.6.9
TRIPLET=aarch64-apple-darwin
curl -LO "https://github.com/qdrddr/clear-your-tools/releases/download/${VERSION}/cyt-indexer-ffi-${TRIPLET}.tar.gz"
mkdir -p cyt-ffi && tar -xzf "cyt-indexer-ffi-${TRIPLET}.tar.gz" -C cyt-ffi
gcc -std=c11 -o myapp main.c -I cyt-ffi -L cyt-ffi -lcyt_indexer
```

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
| `libcyt_indexer.so` / `.dylib` / `cyt_indexer.dll` | Shared library |
| `libcyt_indexer.a` / `cyt_indexer.lib` | Static library |
| `cyt_indexer.h` | C header (also published standalone on the release) |
| `cyt_indexer.dll.lib` | Windows import library (when applicable) |

Verify with `SHA256SUMS` on the release page. On macOS, set `DYLD_LIBRARY_PATH` to the extract directory
when running, or copy the dylib beside your binary.

</details>

## Build from source

From the repository root:

```bash
# Host triplet, release (default)
./sdk/c/scripts/build-c-lib.sh

# Explicit triplet (one of six supported targets)
./sdk/c/scripts/build-c-lib.sh --target x86_64-unknown-linux-gnu

# All six triplets
./sdk/c/scripts/build-c-lib.sh --all
```

Supported triplets:

| Triplet | Platform |
| ------- | -------- |
| `x86_64-unknown-linux-gnu` | Linux x86_64 |
| `aarch64-unknown-linux-gnu` | Linux ARM64 |
| `x86_64-apple-darwin` | macOS x86_64 |
| `aarch64-apple-darwin` | macOS ARM64 |
| `x86_64-pc-windows-msvc` | Windows x86_64 |
| `aarch64-pc-windows-msvc` | Windows ARM64 |

The script copies the generated header to `sdk/c/include/cyt_indexer.h`.

## CMake (recommended)

```bash
cmake -S sdk/c -B sdk/c/build -DCMAKE_BUILD_TYPE=Release \
  -DCYT_RUST_TARGET=$(rustc -vV | sed -n 's/^host: //p')
cmake --build sdk/c/build
ctest --test-dir sdk/c/build --output-on-failure
```

Consumer projects:

```cmake
find_package(CYT REQUIRED)
add_executable(myapp main.c)
target_link_libraries(myapp PRIVATE CYT::cyt_indexer)
```

Or vendored:

```cmake
add_subdirectory(external/clear-your-tools/sdk/c)
target_link_libraries(myapp PRIVATE CYT::cyt_indexer)
```

## Manual link

Use a [prebuilt release archive](#prebuilt-binaries-github-release) or build locally:

```bash
TRIPLET=aarch64-apple-darwin   # match your host or target
./sdk/c/scripts/build-c-lib.sh --target "$TRIPLET"
gcc -std=c11 -o myapp main.c \
  -I sdk/c/include \
  -L "target/$TRIPLET/release" \
  -lcyt_indexer
```

On macOS, set `DYLD_LIBRARY_PATH=target/$TRIPLET/release` when running, or copy the dylib beside the binary.

## Header and memory rules

Include `cyt_indexer.h`:

- Strings returned via `char**` out parameters **must** be freed with `cyt_free_string()`.
- Opaque handles (`CytCatalogBuilder`, `CytDecomposedCatalog`, `CytSkillsBuilder`) **must** be freed with their
  matching `cyt_*_free()` function.
- Error messages are thread-local — call `cyt_get_last_error()` on the same thread that received a non-zero
  error code.

### Policy context JSON (`ctx_json`)

Functions that take `ctx_json` accept a JSON object with:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `system_policy` | string | Policy for system tools (default from runtime config) |
| `mcp_policy` | string | Policy for MCP tools (default from runtime config) |
| `per_tool` | object | Per-tool overrides (`{"Agent": "always_include", ...}`) |
| `tool_kind` | `"system"` \| `"mcp"` | Optional batch override: classify **all** tools as system or MCP instead of inferring from the `mcp__` name prefix |

`tool_kind` is runtime-only (not loaded from YAML config). Use `"mcp"` for executor hook catalogs whose tool ids lack the `mcp__` prefix. Build a context with `cyt_policy_context_from_values` from config, then pass a separate `ctx_json` including `tool_kind` to `cyt_effective_policy`, `cyt_partition_catalog`, and other policy functions.

## Examples

| Example | Demonstrates |
| ------- | ------------ |
| `examples/basic.c` | Catalog build |
| `examples/error_handling.c` | Failure paths, `cyt_get_last_error` |
| `examples/retrieve.c` | Decomposed catalog + `cyt_retrieve_tools` |
| `examples/policies.c` | Partition, merge, policy helpers |
| `examples/skills.c` | PageIndex + BM25 cohesion |

## Related SDKs

- [Go SDK](../go/README.md) — cgo wrapper over the same C library
- [Python SDK](../python/README.md)
- [TypeScript SDK](../typescript/)

## Windows note

`build-c-lib.sh` is a bash script — use Git Bash, WSL, or MSYS2 on Windows. CMake examples copy
`cyt_indexer.dll` beside test binaries automatically.
