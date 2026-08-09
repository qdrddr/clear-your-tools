# Eval harness specification

Minimal evaluation harness interface — **not yet implemented**; spec for first build.

---

## Design goal

Make the paper **data analysis**, not manual result collection:

```python
result = run_task(task=task, agent="cursor", configuration="baseline")
result = run_task(task=task, agent="cursor", configuration="cyt-full")
```

---

## Core API

```python
from evals.cursor.harness import run_task, TaskSpec, RunResult

result: RunResult = run_task(
    task=TaskSpec.load("tasks/task-037.yaml"),
    agent="cursor",           # cursor | claude | codex
    configuration="cyt-full", # baseline | verify-only | cyt-prune | cyt-full
    catalog="catalogs/100-tools.json",
    repetition=0,
    seed=42,
)
```

### `RunResult` schema

```python
@dataclass
class RunResult:
    success: bool
    input_tokens: int
    output_tokens: int
    tool_schema_tokens: int
    tool_calls: int
    malformed_tool_calls: int
    blocked_tool_calls: int
    recovered_tool_calls: int
    tools_available: int
    tools_exposed: int
    properties_available: int
    properties_exposed: int
    enum_values_available: int
    enum_values_exposed: int
    latency_ms: int
    agent_cost: float
    pruner_cost: float
    total_cost: float
    # Metadata
    task_id: str
    configuration: str
    agent: str
    catalog_size: int
    repetition: int
    error: str | None = None
    trace_path: Path | None = None
```

---

## Configuration switching

| `configuration` | CYT setup |
|-----------------|-----------|
| `baseline` | Uninstall CYT hooks; direct MCP backends |
| `verify-only` | `hallucination_gate.enabled: true`, pruning disabled |
| `cyt-prune` | Default hook/proxy pruning, gate off |
| `cyt-full` | Pruning + `hallucination_gate.enabled: true` |

Implement via temporary config overlay in harness (write to temp dir or env `CYT_CONFIG`).

Reference: `src/cyt/config/__init__.py` merge order (defaults → user → CWD → CLI).

---

## Agent runners

### Cursor runner (P0)

1. Ensure cyt-mcp + hooks installed (`cyt hook cursor`)
2. Load task prompt into agent (API or scripted UI — TBD)
3. Collect:
   - Session JSONL: `~/.config/cyt/sessions/`
   - Rules file token count: `.cursor/rules/cyt-injection.mdc`
   - Run assertions from `benchmark-design.md`
4. Teardown fixture state

**Challenge:** Cursor may not expose token usage programmatically — fallback to schema token estimate + manual spot-checks against provider dashboard.

### Claude runner (P1)

Use `cyt launch -- claude` for proxy path; read `stats.db` after task.

### Codex runner (P1)

Use `cyt launch -- codex`; compare against Codex-native (no CYT proxy).

---

## Batch runner

```python
def run_experiment(
    tasks: list[TaskSpec],
    configurations: list[str],
    agents: list[str],
    catalogs: list[Path],
    repetitions: int = 3,
) -> list[RunResult]:
    ...
```

Output: `evals/cursor/results/<timestamp>/results.parquet`

---

## Assertion framework

```python
# evals/cursor/assertions/github.py

def assert_issue_exists(state, *, repo: str, title: str) -> bool:
    ...
```

Tasks reference assertions by name in YAML (see `benchmark-design.md`).

---

## Verification corpus runner (separate from agent tasks)

```python
def run_verification_eval(corpus_path: Path) -> VerificationMetrics:
    """Feed labeled tool calls through tool_gate.validate_pre_tool_call()."""
```

Returns precision, recall, FBR.

Reuse: `src/cyt_client/tool_gate.py`, `schema_validate.py`.

---

## What to build first

Per research recommendation §21 — minimal path:

### Phase 0 — Harness skeleton (1–2 days agent time)

- [ ] `evals/cursor/harness/__init__.py` with `run_task` stub
- [ ] Load YAML task spec
- [ ] Config overlay for 4 configurations
- [ ] Token estimate helper (cl100k on injected schema)
- [ ] Write `RunResult` to JSONL

### Phase 1 — 5 smoke tasks

- [ ] 1 single-tool, 1 enum, 1 optional-property, 1 multi-tool, 1 distractor-heavy
- [ ] Mock MCP server with deterministic responses
- [ ] 4 configurations × 1 repetition

### Phase 2 — Scale to minimal paper eval

- [ ] 50 tasks, 3 catalog sizes, 3 repetitions
- [ ] Cursor only
- [ ] Aggregate script → CSV for Fig 1–3

### Phase 3 — Paper completeness

- [ ] Verification corpus + FBR
- [ ] Recovery tracking
- [ ] Claude/Codex proxy runners
- [ ] Codex native vs Codex+CYT comparison
- [ ] Schema-bloat isolation experiment

---

## Integration with existing tests

| Existing | Reuse for |
|----------|-----------|
| `src/tests/quality_metrics/` | Pricing math, pruning timing, chunk parity |
| Gherkin hallucination features | Verification unit behavior |
| `sdk/e2e/fixtures/bm25_catalog.json` | Catalog fixtures |
| `scripts/analysis/top_tools_by_enums.py` | Enum analysis |

Do **not** conflate unit tests with agent eval — eval harness is end-to-end.

---

## CI considerations

| Tier | Scope | Requirements |
|------|-------|--------------|
| Fast | Verification corpus, token counting on fixtures | No API keys |
| Nightly | 5-task mock MCP eval | Docker |
| Manual | Live sandbox 50-task run | Secrets, $ budget |

---

## Example usage (target)

```bash
# Single task debug
uv run python -m evals.cursor.harness run \
  --task evals/cursor/tasks/task-001.yaml \
  --agent cursor \
  --configuration cyt-prune \
  --catalog evals/cursor/catalogs/100-tools.json

# Full minimal experiment
uv run python -m evals.cursor.harness experiment \
  --tasks evals/cursor/tasks/ \
  --configurations baseline,verify-only,cyt-prune,cyt-full \
  --agents cursor \
  --catalogs 25,100,250 \
  --repetitions 3 \
  --output evals/cursor/results/
```

Add `evals/cursor` to pyproject optional extra or document as `uv run` module path.
