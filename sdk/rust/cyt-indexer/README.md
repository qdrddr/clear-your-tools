# cyt-indexer

Rust library and CLI for tool schema decomposition and catalog indexing (Clear Your Tools).

## Library

```toml
[dependencies]
cyt-indexer = "0.1"
```

```rust
use cyt_indexer::{build_catalog_index, count_tokens, CatalogIndex};
use serde_json::json;

let tools = vec![/* ... */];
let index = build_catalog_index(&tools, &[]);
```

## CLI

```bash
cargo install cyt-indexer
cyt-indexer build --tools tools.json --output ./catalog
cyt-indexer retrieve --catalog ./catalog --input survivors.json --output out.json
```
