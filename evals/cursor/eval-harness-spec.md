# Eval harness specification

**Not yet implemented.** Spec aligned with [evaluation-framework.md](./evaluation-framework.md) — four levels, per-call logging, task verifiers independent of tool metrics.

---

## Core API

```python
from evals.cursor.harness import run_task, TaskSpec, RunResult, ToolCallRecord

result: RunResult = run_task(
    task=TaskSpec.load("tasks/task-037.yaml"),
    agent="cursor",
    configuration="cyt-prune",   # none | verify-only | cyt-prune | cyt-full
    catalog="catalogs/100-tools.json",
    pipeline="bm25",
    repetition=0,
    seed=42,
)
```

---

## Configuration mapping

| `configuration` | Pruning | Verify | Repo |
|-----------------|---------|--------|------|
| `none` | ❌ | ❌ | CYT disabled |
| `verify-only` | ❌ | ✅ | `--prevent-hallucinations` |
| `cyt-prune` | ✅ | ❌ | Default; gate off |
| `cyt-full` | ✅ | ✅ | Gate on |

Primary experiment uses `none` vs `cyt-prune`. Verification experiment uses `none` vs `verify-only`. Smoke runs all four.

---

## Four-level capture

| Level | Harness responsibility |
|-------|------------------------|
| **L1 Schema** | Parse `tool_gate` allow/deny; `schema_valid` on each call |
| **L2 Execution** | Capture MCP/tool backend success/failure (descriptive, not headline) |
| **L3 Trajectory** | Log retries after deny (descriptive) |
| **L4 Task** | Run YAML `verifier` assertions on final state — **sole success bit** |

**Critical:** `RunResult.success` comes **only** from L4 verifier, not from tool-call counts.

---

## ToolCallRecord

| Class | `schema_valid` | `blocked` | `execution_successful` |
|-------|----------------|-----------|------------------------|
| A Valid+successful | True | False | True |
| B Valid+unsuccessful | True | False | False |
| C Malformed+prevented | False | True | None |
| D Malformed+executed | False | False | — (flag anomaly if verify on) |

```python
@dataclass
class ToolCallRecord:
    tool_call_id: str
    task_id: str
    step: int
    tool: str
    cyt_mode: str                  # none | verify | prune | prune+verify
    arguments: dict
    schema_valid: bool             # Level 1
    execution_successful: bool | None  # Level 2
    blocked: bool
    is_retry: bool                 # Level 3 (after deny)
    latency_ms: int
    error: str | None
```

---

## RunResult (task-level)

```python
@dataclass
class RunResult:
    success: bool                      # L4 verifier only
    input_tokens: int
    output_tokens: int
    tool_context_tokens: int           # stubs + injected definitions
    agent_cost: float
    pruner_cost: float                 # BM25 = 0
    total_cost: float
    tool_calls: list[ToolCallRecord]
    mpr: float | None                  # verify configs only
    tools_available: int
    tools_exposed: int
    required_tool_recall: float | None
    task_id: str
    configuration: str
    catalog_size: int
    repetition: int
    latency_ms: int
    error: str | None
    trace_path: Path | None
```

Tokenize via `cyt-indexer-sdk` cl100k_base (consistent with `LIMITATIONS.md`).

v1 uses flat input pricing (no cached/uncached split — see [implementation-status.md](./implementation-status.md)).

---

## Verifier framework (L4)

```python
# evals/cursor/verifiers/github.py
def assert_issue_exists(state, *, repo: str, title: str) -> bool: ...
```

Tasks reference verifiers in YAML — no LLM judge.

---

## Verification corpus runner

```python
def run_verification_eval(corpus: Path) -> dict:
    """Feed labeled calls through tool_gate / schema_validate."""
    return {"mpr": ..., "precision": ..., "recall": ...}
```

Separate from agent `run_task()` — uses static JSONL corpus.

---

## Batch experiments

```python
def run_primary_experiment(...) -> Path:
    """No CYT vs cyt-prune × tasks × catalog sizes."""

def run_verification_experiment(...) -> Path:
    """Static corpus MPR + No CYT vs verify-only on task subset."""
```

Output: `evals/cursor/results/<timestamp>/results.parquet` + `tool_calls.parquet`.

---

## Cursor runner

1. Config overlay for 4 configurations
2. Execute task prompt (API/scripted)
3. Collect: session JSONL, rules file, hook deny events
4. Parse tool calls from agent trace or MCP logs
5. Run L4 verifiers

---

## Phase plan

### Phase 0 — Skeleton

- [ ] `ToolCallRecord`, `RunResult`, config overlay
- [ ] Verification corpus runner (static JSONL)
- [ ] Tool-context token helper (stubs + rules file)

### Phase 1 — 5 smoke tasks

- [ ] Mock MCP + deterministic verifiers
- [ ] All 4 configurations
- [ ] L1–L4 logged; MPR on verify config

### Phase 2 — Primary experiment

- [ ] ~50 Layer B tasks × 3 catalog sizes × 3 reps
- [ ] Cursor only
- [ ] Aggregate → Fig 1–3

---

## CLI (target)

```bash
uv run python -m evals.cursor.harness run \
  --task tasks/task-001.yaml \
  --agent cursor \
  --configuration cyt-prune

uv run python -m evals.cursor.harness experiment primary \
  --tasks tasks/ \
  --catalogs 25,100,250 \
  --repetitions 3
```

---

## Reuse existing tests

| Existing | Use |
|----------|-----|
| `hallucination_gate.feature` | L1 behavior regression |
| `test_pricing.py` | Cost math |
| `test_pruning_timing.py` | Pruning latency baseline |
| `sdk/e2e/fixtures/bm25_catalog.json` | Catalog fixtures |

Do not conflate unit tests with end-to-end eval.
