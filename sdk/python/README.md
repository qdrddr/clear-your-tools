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
from cyt_indexer import (
    PolicyContext,
    apply_tool_kind,
    build_catalog_from_tools,
    coordinate_bm25_prune,
    load_catalog,
    removed_chunks,
    retrieve_tools,
    scoring_policy_context,
)

full = load_catalog(".catalog")
surviving = ...  # survivors.json
removed = removed_chunks(full, surviving)

ctx = PolicyContext("prune_optional", "prune_all")
apply_tool_kind(ctx, "mcp")
scoring = scoring_policy_context(ctx)

result = coordinate_bm25_prune([], full, full, index, "query", scoring, ctx)
```

`ensure_skills_registry` accepts filesystem paths or inline hook/client payloads:

```python
ensure_skills_registry(
    [{"path": "/virtual/skill.md", "content": "---\nname: x\n---\nBody", "content_sha256": "..."}],
    catalog_root,
    pageindex_config,
    pipeline,
    index_params_hash,
)
```
