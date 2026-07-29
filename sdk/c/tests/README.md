# C SDK tests

User-facing samples live under [`../examples/`](../examples/) and run as CTest smoke tests via CMake.

Add regression tests here only for C/CMake-specific concerns that Rust FFI tests do not cover, for example:

- CMake install and packaging layout
- Platform-specific dynamic library load paths (`@loader_path`, Windows DLL staging)
- Header/API drift guards specific to the published C header

Behavioral coverage of `libcyt_indexer` belongs in [`../../rust/cyt-indexer/tests/ffi/`](../../rust/cyt-indexer/tests/ffi/).
