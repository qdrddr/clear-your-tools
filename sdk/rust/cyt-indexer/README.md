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
cyt-indexer build tools --tools /tmp/tools.json --output ./.catalog
cyt-indexer retrieve tools --catalog ./.catalog --input survivors.json --output out.json \
  --config config.json \
  --system-policy prune_optional \
  --mcp-policy prune_all \
  --tool-policy Agent=always_include \
  --tool-policy mcp__fff__multi_grep=always_include \
  --per-tool per-tool.json

# Optional: write non-surviving decomposed chunks (same {json, md} shape as survivors.json)
cyt-indexer retrieve ... --removed-output ./.catalog/removed.json

# Or as a dedicated command (use --full for build_index snapshot vs on-disk catalog)
cyt-indexer removed tools --catalog ./.catalog --input survivors.json --output ./.catalog/removed.json
```

### Skills (markdown pageindex)

```bash
cyt-indexer build skills --skills ~/.claude/skills --output ./.catalog
cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill --query metadata
cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill --query structure
cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill --query content --line_num 5-10
cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill --query content --line_num 5 --line_num 12-15 --node_id 0003
```

Content queries accept repeatable `--line_num` and `--node_id` flags (each value may be a number or
range such as `5-10`). Output defaults to `{catalog}/skill_out.json`; pass `--output` to override.

Build writes `skills/decomposed/{doc_id}/document.json`, `{node_id}.md`, and a reconstructable `skills_index.json` snapshot.

Score filter is **off by default** (rerank survivor scores are ~0.003, not 0–1).
Use `--score-filter` only for LLM-stage catalogs where json scores exceed the decomposed threshold (0.5).

Library:

```rust
use cyt_indexer::{load_catalog_from_dir, removed_chunks, RemovedChunksOptions};

let full = load_catalog_from_dir(".catalog")?;
let surviving: serde_json::Value = /* survivors.json */;
let removed = removed_chunks(&full, &surviving, &RemovedChunksOptions::default());
```

Skills (in-memory, like tools):

```rust
use cyt_indexer::{build_skills_index, get_skill_line_content_from_spec, PageIndexConfig, SkillsBuilder};

let index = build_skills_index(&[PathBuf::from("~/.claude/skills")], &PageIndexConfig::default())?;
let content = get_skill_line_content_from_spec(&index, "my__skill", "5-10");
```

Policies (in precedence order, later wins):

1. `--config` → `pruning.policy.system_tool` / `mcp_tool` (legacy: `defaults.system_tool_policy` / `mcp_tool_policy`), `pruning.per_tool`
2. `--system-policy` / `--mcp-policy` CLI overrides
3. `--per-tool` JSON file: `{"Agent": "always_include", "Bash": "prune_optional"}`
4. `--tool-policy TOOL=POLICY` (repeatable)

Valid policies: `always_include`, `prune_optional`, `prune_all`, `prune_optional_descriptions`, `prune_all_descriptions`.

`retrieve` (CLI and `retrieve_tools_from_catalog`) applies description-policy reinstatement
automatically when per-tool or system/MCP policies use `prune_*_descriptions`. Per-tool overrides
take precedence over system/MCP defaults.
