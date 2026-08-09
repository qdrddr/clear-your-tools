# Metrics and instrumentation

Canonical paper metrics ↔ **clear-your-tools** implementation. See [research-questions.md](./research-questions.md) for formulas.

---

## Canonical metrics table

| Category | Metric | Implemented? | Source / gap |
|----------|--------|--------------|--------------|
| **Task** | Task success rate | ❌ | Harness + verifiers |
| | Task failure rate | ❌ | Derived |
| **Tool calls** | Total tool calls | ❌ | Log in harness |
| | Successful calls | ❌ | L2: MCP result |
| | Unsuccessful calls | ❌ | L2: valid schema, failed exec |
| | Schema-malformed calls | **Partial** | Deny in `tool_gate.py`; not aggregated |
| | Malformed prevented | **Partial** | Same |
| | MPR | ❌ | prevented / malformed |
| | TESR | ❌ | successful / executed |
| **Tokens** | Input tokens | **Partial** | Proxy stats |
| | Output tokens | **Partial** | Proxy stats |
| | Tool-schema tokens | **Partial** | `tools_in/out` in stats |
| | Injected-definition tokens | **Partial** | Tokenize rules file / inject payload |
| | Cached input tokens | ❌ | Need provider cache metadata |
| | Uncached input tokens | ❌ | Same |
| **Cost** | Agent LLM cost | **Partial** | `compute_stats_costs()` |
| | CYT/pruner cost | **Partial** | BM25=$0; rerank/LLM stages |
| | Total input/output cost | **Partial** | Flat rate today |
| | Cost per successful task | ❌ | Harness |
| **Performance** | Task completion time | ❌ | Harness |
| | CYT pruning latency | **Yes** | `test_pruning_timing.py` |
| | Verification latency | ❌ | Hook timing |
| **Pruning** | Tools/properties/enums removed | **Partial** | Counts in stats; enums **gap** |
| | Required-tool/property recall | ❌ | Gold annotations |
| **Recovery** | Recovery rate | ❌ | Multi-turn tracking |
| | Additional calls/tokens to recovery | ❌ | Post-deny overhead |

---

## Per tool-call record (harness must emit)

```python
@dataclass
class ToolCallRecord:
    tool_call_id: str
    task_id: str
    step: int
    client: str                    # cursor | claude | codex
    aggregator: str                # cyt_mcp | mcpc | executor | cloudflare
    mcp_server: str
    tool: str
    cyt_mode: str                  # none | verify | prune | prune+verify
    pruning_pipeline: str          # bm25 | rerank | llm
    schema_exposed: bool           # in Type-1 inject this turn
    schema_source: str             # type1_inject | type2_catalog | deny_exposure
    arguments: dict
    schema_valid: bool             # Level 1
    execution_successful: bool | None  # Level 2; None if prevented
    blocked: bool
    is_retry: bool
    allowed_unexposed: bool        # valid + in Type-2 but not injected
    latency_ms: int
    error: str | None
```

Classify into buckets A/B/C/D per [evaluation-framework.md](./evaluation-framework.md).

---

## Token breakdown (target)

Per LLM request, capture:

```
Input  = user_prompt
       + conversation_context
       + latest_agent_message      # turn-aware pruning eval
       + tool_stubs
       + injected_definitions
       + other

Output = assistant_text + tool_call_arguments
```

### Codebase mapping

| Component | How to measure today |
|-----------|---------------------|
| Tool stubs | Tokenize cyt-mcp minimal schemas |
| Injected defs | `.cursor/rules/cyt-injection.mdc` or proxy inject block |
| User query (proxy prune) | `extract_user_query` in `anthropic.py` |
| Latest agent message | **Gap** — extract from messages[], add to prune query eval |
| cl100k estimate | `cyt-indexer-sdk` / `token_usage.py` |

---

## Cost with prompt caching

Target formula:

\[
\text{Cost} = T_{uncached} P_{input} + T_{cached} P_{cache} + T_{output} P_{output}
\]

| Implementation | Status |
|----------------|--------|
| Stable stub prefix (proxy) | ✅ intentional in `policies.py` |
| Cache token split in stats | ❌ |
| Cache pricing in `pricing.py` | ❌ — extend `models.*.pricing` |

**Paper:** Explicitly model caching; note CYT preserves prefix stability.

---

## Net savings with pruner

Existing: `compute_net_savings_tokens(saved_tokens, total_tokens, costs)` in `pricing.py`.

Extend for:

- Cached/uncached agent input
- Per-pipeline pruner cost (BM25 vs rerank vs LLM)
- CostPerSuccess denominator

---

## Pre-exposure metrics

| Metric | Code hook | Aggregated? |
|--------|-----------|-------------|
| Inject vs skip | `is_pre_exposed()` filter | ❌ |
| Tokens saved on skip | Diff inject payload sizes | ❌ |
| Promotion to full def | N/A — verbatim dedup only | ❌ |

Log from `pre_exposure_pipeline.py` gate decisions in harness.

---

## Compaction metrics

| Event | Code |
|-------|------|
| `preCompact` fired | `is_pre_compact_event()` |
| Session log snapshot | `test_session_compaction.py` pattern |

Compare tool inject counts turn-before vs turn-after compaction.

---

## Proxy stats (Claude/Codex)

`~/.config/cyt/stats.db`:

- `proxy_request`: tool/property counts in/out/pruned
- `tokens`: per-stage usage
- `cyt stats` CLI

Best path for proxy token/cost experiments without custom harness.

---

## Hook stats (Cursor)

| Signal | Path |
|--------|------|
| Session JSONL | Type-1/Type-2 entries |
| Rules file | Post-prune inject size |
| tools-hook endpoints | Limited stats rows |

**Gap:** Parity with proxy stats granularity.

---

## Verification instrumentation

| Event | Where |
|-------|-------|
| Allow/deny | `PreToolValidation` in `tool_gate.py` |
| Deny + exposure | `PreToolDenyExposure` → session log |
| Schema errors | `validate_json_schema()` reason string |

Harness should subscribe to hook outcomes or parse session JSONL for deny events.

---

## Enum counting (gap)

Proxy stats lack enum-value counts. Options:

- Extend Rust pipeline to emit enum counts in prune summary
- Use `scripts/analysis/top_tools_by_enums.py` offline on catalogs
- Parse decomposed chunks from session log

---

## `RunResult` (task-level, harness)

```python
@dataclass
class RunResult:
    # Level 4
    success: bool
    # Tokens (primary result 1)
    input_tokens: int
    output_tokens: int
    tool_context_tokens: int
    injected_definition_tokens: int
    cached_input_tokens: int | None
    uncached_input_tokens: int | None
    # Cost (primary result 2)
    agent_cost: float
    pruner_cost: float
    total_cost: float
    # Aggregates
    tool_calls: list[ToolCallRecord]
    mpr: float | None              # verify configs
    tesr: float | None
    recovery_rate: float | None
    # Pruning
    tools_available: int
    tools_exposed: int
    properties_removed: int
    enums_removed: int | None
    required_tool_recall: float | None
    # Meta
    task_id: str
    configuration: str
    agent: str
    catalog_size: int
    pipeline: str
    latency_ms: int
```

See [eval-harness-spec.md](./eval-harness-spec.md).
