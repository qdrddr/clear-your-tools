# Tier-scoped BM25 prune goldens

Live-tier BM25 regression fixtures (`tools.tiers.mode: live`). Separate from:

- [`../cyt_mcp_catalog/`](../cyt_mcp_catalog/) — tier-off (`shadow`) BM25 goldens
- [`../tier_behavior/`](../tier_behavior/) — tier partition/representation per tier level

## Layout

- `scenarios.json` — scenario ids, base golden reference, tier seeds, must include/exclude tool names
- `input/bm25_tier_<id>.json` — per-scenario metadata (mirrors `scenarios.json` entries)
- `out/bm25_tier_<id>.json` — committed golden pruned output

## Regenerate

```bash
./scripts/local/tests/regenerate-bm25-tier-goldens.sh
```

Or:

```bash
CYT_REGENERATE_BM25_TIER_GOLDENS=1 uv run pytest src/tests/unit/test_cyt_mcp_catalog_bm25_prune_tiered.py -q
```

Re-run when BM25 weights, tier filtering, or T4 merge behavior changes.
