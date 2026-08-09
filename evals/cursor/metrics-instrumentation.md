# Metrics and instrumentation

Mapping of paper metrics to existing code and gaps to implement in the eval harness.

---

## Token metrics

| Metric | Paper symbol | Implemented? | Source |
|--------|--------------|--------------|--------|
| Tool-schema input tokens | \(T^{tools}\) | **Partial** | Proxy: `stats.db` tokens table; hook: estimate from injected rules file / session log |
| Total input tokens | \(T^{input}\) | **Partial** | Proxy upstream request tokens; hook: need agent API usage |
| Output tokens | \(T^{out}\) | **Partial** | Proxy stats |
| Total tokens | | **Partial** | Same |
| Tokens per successful task | | **No** | Needs harness |
| Tool token reduction % | | **Partial** | `cyt stats` for proxy |
| Total input reduction % | | **Partial** | Same |

### Proxy path (Claude/Codex) — best instrumented

`src/cyt/proxy/stats.py` → `proxy_request` columns:

- `tools_in`, `tools_out`, `tools_pruned`
- `tool_count_in/out/pruned`
- `tool_properties_count_in/out/pruned`

Token counting: `src/cyt/common/token_usage.py` (cl100k_base via Rust tiktoken).

**Caveat** (`LIMITATIONS.md`): Estimates may differ from provider billing; pruned content is never sent so savings are real even if estimate differs.

### Hook path (Cursor) — gaps

| Signal | Where | Status |
|--------|-------|--------|
| Injected schema size | `.cursor/rules/cyt-injection.mdc` | Can tokenize post-hoc |
| Stub catalog size | cyt-mcp wire | Minimal `{}` schemas |
| Per-turn prune result | Session JSONL | Parse Type-1 entries |
| Agent-reported usage | Cursor API | **Unknown** — may need manual logging |

---

## Cost metrics

| Metric | Implemented? | Source |
|--------|--------------|--------|
| Agent LLM cost | **Partial** | `compute_stats_costs()` in `pricing.py` |
| BM25 pruner cost | **Yes ($0)** | Local, no API |
| Rerank pruner cost | **Partial** | Stats stage tokens × DeepInfra pricing |
| LLM pruner cost | **Partial** | Stats stage tokens × model pricing |
| Net cost reduction | **Partial** | `compute_net_savings_tokens()` |
| Cost per successful task | **No** | Needs harness + success bit |

### Pricing config

Model pricing in `defaults.yaml` → `models.llm.remote[].pricing`.

User overrides in `~/.config/cyt/config.yaml`.

---

## Pruning quality metrics

| Metric | Implemented? | Notes |
|--------|--------------|-------|
| Tools available vs exposed | **Partial** | Stats counts; not per-task gold recall |
| Properties available vs exposed | **Partial** | `tool_properties_count_*` |
| Enum values available vs exposed | **No** | Rust prunes enums; no aggregate enum count in stats |
| Tool recall vs \(G_t\) | **No** | Needs gold annotations |
| Property recall | **No** | Same |
| Enum recall | **No** | Same |

### Irrelevant-token removal table (paper Table)

| | Before | After | Reduction |
|---|--------|-------|-----------|
| Tools | stats `tool_count_in/out` | ✅ proxy |
| Properties | stats `tool_properties_count_*` | ✅ proxy |
| Enum values | — | **Build** |
| Tool-schema tokens | stats tokens | ✅ proxy |

---

## Verification metrics

| Metric | Implemented? | Source |
|--------|--------------|--------|
| Malformed call detected | **Partial** | Deny in `tool_gate.py`; not aggregated |
| Blocked tool calls | **Partial** | Session log / hook stderr |
| Precision / Recall / FBR | **No** | Needs verification corpus runner |
| False blocking rate | **No** | Critical — build first for verify-only eval |

### Existing behavioral tests

- `src/tests/unit/gherkin/features/hallucination_gate.feature`
- `src/tests/unit/gherkin/features/tool_catalog_gate.feature`
- `src/cyt_client/schema_validate.py` unit tests

---

## Recovery metrics

| Metric | Implemented? | Source |
|--------|--------------|--------|
| Deny + schema exposure | **Yes** | `PreToolDenyExposure`, `session_pre_tool_exposure.py` |
| Recovery rate | **No** | Track multi-turn after deny |
| Recovery overhead (tokens/calls/latency) | **No** | Harness |

---

## Task quality metrics

| Metric | Implemented? | Source |
|--------|--------------|--------|
| Deterministic success/failure | **No** | Benchmark harness |
| Success rate by config | **No** | Harness aggregation |
| Success rate by catalog size | **No** | Catalog sweep runner |

---

## Operational metrics (supporting)

| Metric | Implemented? | Source |
|--------|--------------|--------|
| Pruning latency | **Yes** | `test_pruning_timing.py` |
| Removed chunks parity | **Yes** | `test_removed_chunks.py`, Rust parity tests |
| Pipeline stage breakdown | **Yes** | `PRUNING_STAT_STAGES` in token_usage |

---

## `cyt stats` CLI

Aggregates `~/.config/cyt/stats.db` for proxy runs:

```bash
cyt stats
cyt stats --json  # if supported
```

**Use for:** Claude/Codex proxy experiments out of the box.

**Extend for:** Hook path exports, per-task correlation, eval batch ingestion.

---

## Harness instrumentation checklist

When implementing `run_task()`, capture at minimum:

```python
{
    "success": bool,
    "input_tokens": int,
    "output_tokens": int,
    "tool_schema_tokens": int,
    "tool_calls": int,
    "malformed_tool_calls": int,
    "blocked_tool_calls": int,
    "recovered_tool_calls": int,
    "tools_available": int,
    "tools_exposed": int,
    "properties_available": int,
    "properties_exposed": int,
    "enum_values_available": int,
    "enum_values_exposed": int,
    "latency_ms": int,
    "agent_cost": float,
    "pruner_cost": float,
    "total_cost": float,
    # Extensions for paper:
    "configuration": str,      # baseline | verify-only | cyt-prune | cyt-full
    "catalog_size": int,
    "task_id": str,
    "run_id": str,
    "agent": str,              # cursor | claude | codex
}
```

See [eval-harness-spec.md](./eval-harness-spec.md).

---

## Enum counting (to implement)

Add helper using existing Rust pipeline:

```python
from cyt.indexer.build import collect_enums  # re-export in build.py
```

Or parse decomposed catalog chunks from session JSONL for hook-path enum counts.
