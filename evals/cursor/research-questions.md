# Research questions and metrics

Three **primary results** (cost, tokens, task quality) with four **evaluation levels** as supporting structure. See [evaluation-framework.md](./evaluation-framework.md).

---

## Primary result 1 — Token consumption

**Do not use "total tokens" as a single headline.**

### Report separately

**Input:**

| Component | CYT impact |
|-----------|------------|
| User prompt tokens | Unchanged |
| Latest agent-message tokens | Capture for turn-aware analysis |
| Tool-schema tokens (stubs on wire) | Reduced via stubs |
| Injected tool-definition tokens | Reduced via pruning |
| Other context tokens | Unchanged |

**Output:**

| Component | Notes |
|-----------|-------|
| Assistant output tokens | Unchanged by CYT |
| Tool-call argument tokens | May change with recovery |

### Headline token metrics

| Metric | Formula |
|--------|---------|
| Tool-context input tokens | stubs + injected definitions |
| Total input tokens | Full request |
| Tool-context reduction | \(1 - T_{CYT}^{toolctx} / T_{baseline}^{toolctx}\) |
| Total input reduction | \(1 - T_{CYT}^{input} / T_{baseline}^{input}\) |

Verify-only: tool-context reduction ≈ 0%.

### Codebase

| Signal | Source |
|--------|--------|
| Proxy tool in/out tokens | `stats.db` via `cyt stats` |
| Injected rules file | Tokenize `.cursor/rules/cyt-injection.mdc` |
| Input breakdown | **Gap** — not decomposed in stats today |

---

## Primary result 2 — Cost

### Cache-aware cost model

Don't use `input_tokens × input_price` alone when prefix is stable.

\[
\text{Cost} = T_{input,uncached} P_{input} + T_{input,cached} P_{cached} + T_{output} P_{output}
\]

Use provider cache-read/cache-write rates when available (Anthropic prompt caching, etc.).

CYT proxy preserves stable stub prefix intentionally — **cache modeling is required** for fair comparison.

### Net cost with pruner

\[
C_{CYT} = C_{pruning} + C_{agent} + C_{infra}
\]

\[
\text{NetSavings} = C_{baseline} - C_{CYT}
\]

| Pruner | \(C_{pruning}\) in repo |
|--------|-------------------------|
| BM25 | $0 |
| Rerank | LiteLLM stage in `pricing.py` |
| LLM | LiteLLM stage in `pricing.py` |

**Key comparison:** expensive LLM selects tools (baseline context bloat) vs cheap LLM prunes + expensive LLM tasks.

### Headline cost metric

\[
\text{CostPerSuccess} = \frac{\text{TotalCost}}{\text{SuccessfulTasks}}
\]

### Codebase

`compute_stats_costs()`, `compute_net_savings_tokens()` — partial; no cached/uncached split.

---

## Primary result 3 — Task quality

**Independent of tool-call metrics.**

\[
\text{TaskSuccessRate} = \frac{\text{SuccessfulTasks}}{\text{TotalTasks}}
\]

Each task: deterministic verifier on final environment state.

| Task type | Verifier example |
|-----------|------------------|
| Create issue | issue exists, repo, title, body match |
| Find PR #123 | correct PR returned |
| Multi-step | issue exists, correct repo, release referenced |

**No LLM judge** where deterministic checks suffice.

---

## Supporting — Tool-call metrics (Levels 1–2)

Separate from task success. Classify every call → [evaluation-framework.md](./evaluation-framework.md).

| Metric | Formula |
|--------|---------|
| Malformed-call prevention rate (MPR) | prevented malformed / malformed |
| Tool execution success rate (TESR) | successful / executed (exclude prevented) |
| Schema-malformed rate | malformed / total |
| Execution-unsuccessful rate | valid schema, failed backend / total |

Report absolute counts **and** percentages for each bucket (A/B/C/D).

---

## Supporting — Agent trajectory (Level 3)

| Metric | Definition |
|--------|------------|
| Recovery rate | Tasks with schema-invalid call that eventually succeed |
| Recovery overhead | Extra calls, input/output tokens, latency after deny |

**Code:** Deny path in `tool_gate.py`; **gap:** aggregate metrics.

---

## Supporting — Pruning quality

Gold set \(G_t\) vs CYT selected \(P_t\) per task:

| Metric | Formula |
|--------|---------|
| Required-tool recall | \(\|G_t^{tools} \cap P_t^{tools}\| / \|G_t^{tools}\|\) |
| Required-property recall | Same for properties |
| Required-enum recall | Same for enums |

Irrelevant-token removal table (tools / properties / enums / tool-schema tokens).

### Pre-exposure / promotion

| Metric | Notes |
|--------|-------|
| Times tool requested | Per session |
| Times definition injected vs skipped | Pre-exposure filter |
| Tokens saved by skip | Compare re-inject vs skip |
| Task success with/without pre-exposure | Ablation |

### Compaction ablation

Before vs after `preCompact`: tool availability, re-inject count, task success.

---

## Supporting — Pruning pipeline axis

Independent experiment — same tasks, different pipeline:

| Pipeline | Code |
|----------|------|
| BM25 | `src/cyt/pruners/bm25.py` |
| Rerank | `src/cyt/pruners/rerank.py` |
| LLM | `src/cyt/pruners/llm.py` |

Measure: recall, token reduction, latency, pipeline cost, task success.

**Hypothesis to test:** BM25 provides most benefit at fraction of pruning cost.

---

## Supporting — Schema admission (secondary)

How often does the model call a tool **not injected** but in Type-2 catalog with valid schema?

Log `allowed_unexposed` rate — interesting for "tools in weights" discussion.

---

## Canonical metrics table (paper)

| Category | Metric |
|----------|--------|
| **Task** | Task success rate, task failure rate |
| **Tool calls** | Total, successful, unsuccessful, schema-malformed, malformed prevented, MPR, TESR |
| **Tokens** | Input, output, tool-schema, injected-definition, cached input, uncached input |
| **Cost** | Agent LLM, CYT/pruner, total input/output cost, cost per successful task |
| **Performance** | Task completion time, CYT pruning latency, verification latency |
| **Pruning** | Tools/properties/enums removed, required-tool/property recall |
| **Recovery** | Recovery rate, additional calls/tokens to recovery |

Do not add metrics beyond this unless data demands it.

---

## Statistical reporting

Paired design: same task, model, temperature, seed; N=5–10 repetitions.

Report mean, median, std dev, 95% CI.
