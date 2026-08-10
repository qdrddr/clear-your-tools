# Evaluation framework

Core conceptual design: **separate tool-call correctness from task correctness**. A tool call can be syntactically valid but semantically wrong; a task can still succeed after retries.

---

## Three primary configurations

| Configuration | Tool pruning | Verify/prevent | Purpose |
|---------------|--------------|----------------|---------|
| **No CYT** | ❌ | ❌ | Baseline |
| **CYT Verify-Prevent** | ❌ | ✅ | Malformed-call prevention + recovery |
| **CYT Pruning** | ✅ | Optional | Context reduction + task quality |

Verify-Prevent may be **disabled** inside the pruning configuration → yields a four-way ablation:

| Configuration | Pruning | Verify | Repo setup |
|---------------|---------|--------|------------|
| Baseline | ❌ | ❌ | No CYT / disabled |
| Verify only | ❌ | ✅ | `cyt hook <agent> --prevent-hallucinations` |
| Pruning only | ✅ | ❌ | Default hook/proxy; gate off |
| Pruning + Verify | ✅ | ✅ | Pruning + `hallucination_gate.enabled: true` |

First three are **primary experiments**; fourth is full CYT.

---

## Four evaluation levels

```
                    Task Success          ← Level 4 (ultimate quality metric)
                         ↑
                  Agent trajectory        ← Level 3 (recovery after block)
                         ↑
               Tool execution success     ← Level 2 (backend result)
                         ↑
                 Schema correctness       ← Level 1 (Verify-Prevent)
```

### Level 1 — Tool-call shape (schema correctness)

Did arguments match the declared tool schema?

- Missing required property
- Wrong type
- Invalid enum
- Malformed object structure

**Caught by:** Verify-Prevent (`src/cyt_client/schema_validate.py`, `tool_gate.py`).

**Terminology:** **Schema-invalid tool call** — not "incorrect data."

### Level 2 — Tool-call execution

Did the tool actually execute successfully?

Valid schema but backend failed (wrong repo, missing resource, auth error, etc.).

Verify-Prevent does **not** catch Level 2 failures. **v1 eval:** log descriptively; not a headline metric — CYT doesn't intervene at L2.

### Level 3 — Agent trajectory

Did the agent recover after a schema-invalid deny?

```
tool call → schema-invalid → CYT blocks → agent receives schema → corrected call → success
```

**Codebase:** Deny + schema exposure via `PreToolDenyExposure`, `session_pre_tool_exposure.py`.

**v1 eval:** log retries; recovery rate/overhead deferred (MPR covers verify value).

### Level 4 — Task outcome

Did the agent accomplish the user's requested task?

**Independent** of tool-call metrics. Deterministic task-specific verifier only — not LLM-as-judge.

\[
\text{TaskSuccessRate} = \frac{\text{SuccessfulTasks}}{\text{TotalTasks}}
\]

---

## Tool-call classification

Capture **every** tool call:

| Field | Description |
|-------|-------------|
| `tool_call_id` | Unique per call |
| `task_id`, `step` | Task and turn index |
| `tool` | Tool name |
| `CYT mode` | baseline / verify-only / prune / full |
| `arguments` | Raw args |
| `schema-valid?` | Level 1 |
| `execution-successful?` | Level 2 (descriptive) |
| `blocked?`, `retry?` | Verify path / L3 |
| `latency` | ms |

### Four buckets

| Class | Condition |
|-------|-----------|
| **A. Valid + successful** | Schema valid ∧ execution successful |
| **B. Valid + unsuccessful** | Schema valid ∧ execution failed — *Verify-Prevent doesn't catch* |
| **C. Malformed + prevented** | Schema invalid ∧ CYT blocked |
| **D. Malformed + executed** | Schema invalid ∧ reached tool — *should be ≈0 with verify enabled* |

---

## Tool-call metrics (report absolute + percentage)

For each category report **count** and **rate vs total** and **rate vs attempted** where applicable.

| Metric | Formula | Notes |
|--------|---------|-------|
| Schema-malformed calls | malformed / total | Level 1 |
| **Malformed-call prevention rate (MPR)** | prevented malformed / malformed | **Headline for verify experiment** |

---

## Schema-based tool-call admission

CYT is **not** a strict "only exposed tools" allowlist for schema validation.

The gate validates against the session **Type-2 catalog** (full backend catalog), not Type-1 (injected fragments):

```897:897:src/cyt_client/tool_gate.py
    """Return validation outcome. Type-2 catalog is the only authority for gating."""
```

A model may call a tool **not injected this turn** but present in Type-2 with valid args → **allowed**. This is **schema-based admission**, not exposure enforcement.

Document in paper; measuring unexposed-but-allowed rate deferred — see [implementation-status.md](./implementation-status.md).

---

## Three primary paper results

1. **Cost** — net agent + pruner + infra, with cache-aware pricing
2. **Token consumption** — input/output split; tool-context vs total input
3. **Task quality** — TaskSuccessRate via deterministic verifiers

Supporting: MPR (L1), required-tool recall, pruning latency.

---

## Core research question (sharpened)

> Can an agent operate effectively with substantially less tool-context when irrelevant tools, optional schema properties, and enum values are dynamically removed, without degrading task completion — and can schema-level verification recover from malformed tool calls when the model produces calls outside the currently exposed context?

---

## Central narrative

```
         MCP / Tool-rich agents
                  │
                  ▼
          Tool-context bloat
                  │
     ┌────────────┴────────────┐
     │                         │
Cost / tokens            Context dilution
     │                         │
     └────────────┬────────────┘
                  ▼
            Clear Your Tools
                  │
     ┌────────────┴────────────┐
     │                         │
  Pruning                 Verification
     │                         │
Tools/properties/         Schema-invalid
    enums                      calls
     │                         │
     ▼                         ▼
Smaller context          Prevent + recover
     │
     ▼
Lower input cost + preserved quality
```

**Do not claim CYT proves context rot.** Frame as: CYT tests whether reducing irrelevant tool context improves efficiency and preserves or improves task completion.
