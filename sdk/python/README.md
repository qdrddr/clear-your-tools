# cyt-indexer-sdk

Python bindings for the [cyt-indexer](https://crates.io/crates/cyt-indexer) Rust library.

Platforms supported: Linux/Windows11/macOS
CPU Architectures: x86/ARM

## Development

```bash
cd sdk/python
uv sync
uv run maturin develop --release
```

## Usage

```python
from cyt_indexer.build import build_catalog_index, CatalogIndex
from cyt_indexer.tokens import count_tokens
```
