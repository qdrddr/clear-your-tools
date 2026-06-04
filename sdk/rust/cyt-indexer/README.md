# cyt-indexer

Rust library and CLI for tool schema decomposition and catalog indexing (Clear Your Tools).

## Library

```toml
[dependencies]
cyt-indexer = "0.1"
```

```rust
use cyt_indexer::{build_catalog_index, CatalogIndex};
use serde_json::json;

let tools = vec![/* ... */];
let index = build_catalog_index(&tools, &[]);
```

## CLI

Accepts Anthropic API tools (`name`, `description`, `input_schema`) or pre-built catalog entries (`id`, `full_schema`).

```bash
cargo install cyt-indexer
jq '.body.tools' debug/full_example.json > /tmp/tools.json
cyt-indexer build --tools /tmp/tools.json --output ./.catalog
cyt-indexer retrieve --catalog ./.catalog --input survivors.json --output out.json \
  --config config.json \
  --system-policy prune_optional \
  --mcp-policy prune_all \
  --tool-policy Agent=always_include \
  --tool-policy mcp__fff__multi_grep=always_include \
  --per-tool per-tool.json

# Optional: write non-surviving decomposed chunks (same {json, md} shape as survivors.json)
cyt-indexer retrieve ... --removed-output ./.catalog/removed.json

# Or as a dedicated command (use --full for build_index snapshot vs on-disk catalog)
cyt-indexer removed --catalog ./.catalog --input survivors.json --output ./.catalog/removed.json
```

Score filter is **off by default** (rerank survivor scores are ~0.003, not 0–1).
Use `--score-filter` only for LLM-stage catalogs where json scores exceed the decomposed threshold (0.5).

Library:

```rust
use cyt_indexer::{load_catalog_from_dir, removed_chunks, RemovedChunksOptions};

let full = load_catalog_from_dir(".catalog")?;
let surviving: serde_json::Value = /* survivors.json */;
let removed = removed_chunks(&full, &surviving, &RemovedChunksOptions::default());
```

Policies (in precedence order, later wins):

1. `--config` → `defaults.system_tool_policy`, `defaults.mcp_tool_policy`, `pruning.per_tool`
2. `--system-policy` / `--mcp-policy` CLI overrides
3. `--per-tool` JSON file: `{"Agent": "always_include", "Bash": "prune_optional"}`
4. `--tool-policy TOOL=POLICY` (repeatable)

Valid policies: `always_include`, `prune_optional`, `prune_all`.
