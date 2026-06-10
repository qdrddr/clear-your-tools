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
cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill --query content --line_num 5 --line_num 12-15 --node_id 3
```

Content queries accept repeatable `--line_num` and `--node_id` flags (each value may be a number or
range such as `5-10`). Output defaults to `{catalog}/skill_out.json`. A relative `--output`
filename (for example `skill_out.json`) is written under `--catalog`; absolute paths are used as given.
The JSON includes `matched_node_ids` (query hits), `node_ids` (hits plus ancestor nodes),
`nodes` (restored per-node content), `restored_markdown`, and `restored_path`.

Content retrieve also writes a pruned, reconstructed skill markdown under
`{catalog}/skills/retrieve/{skill-dir}/{filename}.md` (for example `skills/retrieve/lean-ctx/SKILL.md`).
Matched nodes plus all ancestor nodes in the document tree are included; sibling sections are omitted.
Pass `--keep-all-headers` to include every section heading in that restored file (unmatched
sections keep the heading line only; body text is still omitted).
Build stores each skill's YAML `frontmatter` and preamble in `document.json`; content retrieve
uses that catalog snapshot (not the live file) so `name`/`description` match what was indexed.
Falls back to the indexed path when older catalogs lack those fields.

Build writes `skills/decomposed/{doc_id}/document.json`, `{node_id}.md` (numeric ids `0`, `1`, …), and a
reconstructable `skills_index.json` snapshot. YAML frontmatter is always node `0` when present; preamble
text (after frontmatter, before the first heading) is always node `1` when present. Heading sections
start at node `2`, even when frontmatter and/or preamble are absent.

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

## BM25 Cohesion Chunker

Lexical (BM25 + Snowball) text chunker for skills pageindex and standalone use. Mirrors the
[Chonkie SemanticChunker](https://docs.chonkie.ai/oss/chunkers/semantic-chunker) pipeline
(Savitzky–Golay smoothing, split filtering, optional skip-window merge) but uses BM25
cohesion instead of embeddings — no model calls, deterministic, fast.

During skills build, each section gets at least one chunk when BM25 chunking is enabled
(default). Sections larger than `chunk_size` are split; chunk files land at
`skills/decomposed/{doc_id}/chunks/{chunk_id}.md` with `chunks: [{chunk_id}]` in
`document.json`. Parent `{node_id}.md` keeps the full section text.

**Default (BM25 chunking on):** returns both node-level files and BM25 chunk files.

**Node-level only (no BM25 chunking):** set `enable_bm25_chunking: false` or
`chunk_size: 0`. The SDK returns the pageindex tree and per-node markdown only — BM25
cohesion is not run, so a host app can apply its own chunking strategy. Use
`PageIndexConfig::without_bm25_chunking()` (Rust), `page_index_config_without_chunking()`
(Python), or `pageIndexConfigWithoutChunking()` (TypeScript).

### BM25 cohesion CLI

```bash
cyt-indexer build skills --skills ~/.claude/skills --output ./.catalog \
  --window-mode sentence --chunk-size 2048 --similarity-window 3 \
  --token-counter approximate --skip-window 0

cyt-indexer retrieve skills --catalog ./.catalog --doc-id my__skill \
  --query content --chunk_id 8 --chunk_id 10-12
```

### Rust

```rust
use cyt_indexer::{
    Bm25CohesionChunker, Bm25CohesionConfig, PageIndexConfig, build_skills_index,
};

// Standalone chunker
let chunks = Bm25CohesionChunker::new(Bm25CohesionConfig::default())?.chunk(long_text);

// Pageindex with partial override
let config = PageIndexConfig::from_value(&serde_json::json!({
    "bm25_cohesion": { "window_mode": "word", "skip_window": 1 }
}));
let index = build_skills_index(&[PathBuf::from("~/.claude/skills")], &config)?;
```

### Python

```python
from cyt_indexer import Bm25CohesionConfig, PageIndexConfig, bm25_cohesion_chunk, build_skills_index
from cyt_indexer import page_index_config_without_chunking

chunks = bm25_cohesion_chunk(text, Bm25CohesionConfig(skip_window=1))
index = build_skills_index(["~/.claude/skills"], {"bm25_cohesion": {"chunk_size": 1024}})
# Node-level only — skip BM25 chunking during build:
index = build_skills_index(["~/.claude/skills"], page_index_config_without_chunking().to_dict())
```

### TypeScript

```typescript
import {
  bm25CohesionChunk,
  buildSkillsIndex,
  defaultBm25CohesionConfig,
  pageIndexConfigWithoutChunking,
} from "@clear-your-tools/cyt-indexer";

const chunks = bm25CohesionChunk(text, { ...defaultBm25CohesionConfig(), skipWindow: 1 });
const index = buildSkillsIndex(["~/.claude/skills"], { bm25Cohesion: { chunkSize: 1024 } });
// Node-level only — skip BM25 chunking during build:
const nodeOnly = buildSkillsIndex(["~/.claude/skills"], pageIndexConfigWithoutChunking());
```

### Key parameters

| Field | Default (sentence) | Default (word) | Notes |
| ----- | ------------------ | -------------- | ----- |
| `enable_bm25_chunking` | `true` | `true` | `false` skips BM25 chunking (node-level only) |
| `window_mode` | `sentence` | — | `sentence` or `word` |
| `chunk_size` | 2048 | 2048 | Max tokens per chunk; `0` also disables chunking |
| `similarity_window` | 3 | 500 | Left-window size (sentences vs words) |
| `threshold` | 0.8 | 0.8 | Percentile for boundary filter |
| `skip_window` | 0 | 0 | SDPM merge pass (`0` = off) |
| `token_counter` | `approximate` | `approximate` | `approximate` or `character` |

### Trade-offs vs embedding chunkers

- **Pros:** No API/model cost, deterministic, aligns with cyt-indexer BM25 pruner tokenization
- **Cons:** Topic boundaries follow lexical overlap, not semantic similarity; code-heavy
  sections may split mid-block in sentence mode (word mode is often better for code)

Policies (in precedence order, later wins):

1. `--config` → `pruning.policy.system_tool` / `mcp_tool` (legacy: `defaults.system_tool_policy` / `mcp_tool_policy`), `pruning.per_tool`
2. `--system-policy` / `--mcp-policy` CLI overrides
3. `--per-tool` JSON file: `{"Agent": "always_include", "Bash": "prune_optional"}`
4. `--tool-policy TOOL=POLICY` (repeatable)

Valid policies: `always_include`, `prune_optional`, `prune_all`, `prune_optional_descriptions`, `prune_all_descriptions`.

`retrieve` (CLI and `retrieve_tools_from_catalog`) applies description-policy reinstatement
automatically when per-tool or system/MCP policies use `prune_*_descriptions`. Per-tool overrides
take precedence over system/MCP defaults.
