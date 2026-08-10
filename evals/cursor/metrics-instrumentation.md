# Metrics and instrumentation

Canonical paper metrics ↔ **clear-your-tools** implementation. See [research-questions.md](./research-questions.md) for
formulas.

---

## Canonical metrics table

| Category | Metric | Implemented? | Source / gap |
| ---------- | -------- | -------------- | -------------- |
| **Task** | Task success rate | ❌ | Harness + verifiers |
| **Tool calls** | Total tool calls | ❌ | Log in harness |
| | Schema-malformed calls | **Partial** | Deny in `tool_gate.py`; not aggregated |
| | Malformed prevented | **Partial** | Same |
| | MPR | ❌ | prevented / malformed |
| **Tokens** | Input tokens | **Partial** | Proxy stats; hook estimate |
| | Output tokens | **Partial** | Proxy stats |
| | Tool-context tokens | **Partial** | stubs + rules file tokenize |
| | Injected-definition tokens | **Partial** | Tokenize rules file |
| **Cost** | Agent LLM cost | **Partial** | `compute_stats_costs()` |
| | CYT/pruner cost | **Partial** | BM25=$0 |
| | Cost per successful task | ❌ | Harness |
| **Performance** | Task completion time | ❌ | Harness |
| | CYT pruning latency | **Yes** | `test_pruning_timing.py` |
| **Pruning** | Tools exposed vs available | **Partial** | Counts in stats |
| | Required-tool recall | ❌ | Gold `tools` in task YAML |

Deferred metrics (cached/uncached split, recovery rate, TESR, property/enum recall, pre-exposure aggregates) — see [implementation-status.md](./implementation-status.md).

---

## Per tool-call record (harness must emit)

```python
@dataclass
class ToolCallRecord:
    tool_call_id: str
    task_id: str
    step: int
    tool: str
    cyt_mode: str
    arguments: dict
    schema_valid: bool
    execution_successful: bool | None
    blocked: bool
    is_retry: bool
    latency_ms: int
    error: str | None
```

Classify into buckets A/B/C/D per [evaluation-framework.md](./evaluation-framework.md).

---

## Token capture

Per run:

```text
tool_context = tokenize(stubs) + tokenize(injected_definitions)
total_input  = from trace or estimate
total_output = from trace or estimate
```

| Component | How to measure |
| ----------- | ---------------- |
| Tool stubs | Tokenize cyt-mcp minimal schemas |
| Injected defs | `.cursor/rules/cyt-injection.mdc` |
| cl100k estimate | `cyt-indexer-sdk` / `token_usage.py` |

---

## Net savings with pruner

Existing: `compute_net_savings_tokens()` in `pricing.py`.

v1: flat input pricing; BM25 pruner cost = $0.

---

## Hook stats (Cursor)

| Signal | Path |
| -------- | ------ |
| Session JSONL | Type-1/Type-2 entries |
| Rules file | Post-prune inject size |
| Allow/deny | `preToolUse` → `tool_gate.py` |

Harness parses session JSONL for deny events and inject sizes.

---

## Proxy stats (follow-up — Claude/Codex)

`~/.config/cyt/stats.db` — richer token data when extending beyond Cursor v1.

---

## `RunResult` (task-level, harness)

See [eval-harness-spec.md](./eval-harness-spec.md).
