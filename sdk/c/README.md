# cyt-indexer C SDK

Pure C integration for [cyt-indexer](../rust/cyt-indexer/) — tool schema decomposition and catalog indexing for
MCP tool gating.

This package links the shared C library (`libcyt_indexer` / `cyt_indexer.dll`) generated from the Rust crate's `ffi` feature.

## Prerequisites

- Rust toolchain (`cargo`, `rustup`)
- C11 compiler (GCC, Clang, or MSVC)
- CMake 3.16+ (recommended)

## Build the shared library

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
