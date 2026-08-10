# Research questions and metrics

Three **primary results** (cost, tokens, task quality) with Level 1 tool-call metrics as supporting evidence for the verification
experiment. See [evaluation-framework.md](./evaluation-framework.md).

---

## Primary result 1 — Token consumption

### Report

| Metric | Notes |
| -------- | ------- |
| Tool-context input tokens | stubs + injected definitions — **headline** |
| Total input tokens | Full request |
| Output tokens | Per run |
| Tool-context reduction | \(1 - T_{CYT}^{toolctx} / T_{baseline}^{toolctx}\) |
| Total input reduction | \(1 - T_{CYT}^{input} / T_{baseline}^{input}\) |

Verify-only: tool-context reduction ≈ 0%.

### Codebase

| Signal | Source |
| -------- | -------- |
| Proxy tool in/out tokens | `stats.db` via `cyt stats` |
| Injected rules file | Tokenize `.cursor/rules/cyt-injection.mdc` |
| Tool-context total | stubs + injected (harness computes) |

Detailed per-component input breakdown and cache splits deferred — see [implementation-status.md](./implementation-status.md).

---

## Primary result 2 — Cost

### Net cost with pruner (v1: flat input pricing)

\[
C_{CYT} = C_{pruning} + C_{agent}
\]

\[
\text{NetSavings} = C_{baseline} - C_{CYT}
\]

| Pruner | \(C_{pruning}\) |
| --- | --- |
| BM25 (v1 default) | $0 |

### Headline cost metric

\[
\text{CostPerSuccess} = \frac{\text{TotalCost}}{\text{SuccessfulTasks}}
\]

### Cost signals

`compute_stats_costs()`, `compute_net_savings_tokens()` — use flat rates for v1 Cursor eval.

## Primary result 3 — Task quality

**Independent of tool-call metrics.**

\[
\text{TaskSuccessRate} = \frac{\text{SuccessfulTasks}}{\text{TotalTasks}}
\]

Each task: deterministic verifier on final environment state. No LLM judge.

---

## Supporting — Verify-Prevent (Level 1)

| Metric | Formula |
| ------ | ------- |
| **MPR** (malformed-call prevention rate) | prevented malformed / malformed |

Static verification corpus + agent runs with verify-only config.

Classify calls into buckets A/B/C/D ([evaluation-framework.md](./evaluation-framework.md)) — report counts; MPR is the
headline for verify experiment.

---

## Supporting — Pruning quality

Gold set \(G_t\) vs CYT selected \(P_t\) per task:

| Metric | Formula |
| --- | --- |
| Required-tool recall | recall of gold tools in pruned set |

From task YAML `gold.tools` — no property/enum gold annotations in v1. Formula: \(\|G_t^{tools} \cap P_t^{tools}\| /
\|G_t^{tools}\|\).

---

## Canonical metrics table (paper)

| Category | Metric |
| ---------- | -------- |
| **Task** | Task success rate |
| **Tool calls** | Total, schema-malformed, malformed prevented, MPR |
| **Tokens** | Input, output, tool-context, injected-definition |
| **Cost** | Agent LLM, CYT/pruner (BM25=$0), total, cost per successful task |
| **Performance** | Task completion time, CYT pruning latency |
| **Pruning** | Tools exposed vs available, required-tool recall |

Do not add metrics beyond this unless data demands it.

---

## Statistical reporting

Paired design: same task, model, temperature, seed; N=3 repetitions (v1).

Report mean, median, std dev, 95% CI.
