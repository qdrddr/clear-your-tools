# Research questions

Three headline RQs for the arXiv evaluation, with metrics and codebase instrumentation notes.

---

## RQ1 — Token efficiency

**Question:** How much does CYT reduce tool-schema and **total input-token** consumption compared with exposing the complete tool catalog?

### Primary metrics

| Metric | Formula / definition |
|--------|---------------------|
| Tool token reduction | \(1 - T_{CYT}^{tools} / T_{baseline}^{tools}\) |
| Total input reduction | \(1 - T_{CYT}^{input} / T_{baseline}^{input}\) |
| Tokens per successful task | Total tokens / successful tasks |
| Output tokens | Report separately (CYT should not reduce) |

### Also report

- Tool-schema input tokens
- Total input tokens
- Output tokens
- Total tokens
- Percentage reduction (tool-only **and** total)

### Critical distinction

Reviewers will ask whether tool schemas were a significant fraction of the request. **Always report total input reduction**, not only tool-schema reduction.

### Codebase today

| Source | Coverage |
|--------|----------|
| `src/cyt/proxy/stats.py` | Rich for **proxy path**: `tools_in/out`, property counts, token rows |
| `src/cyt/common/token_usage.py` | cl100k_base estimates via `cyt-indexer-sdk` |
| `cyt stats` CLI | Aggregates proxy stats from `~/.config/cyt/stats.db` |
| Hook path (Cursor) | **Gap** — need harness to capture injected schema size per turn |

### Verify-only expectation

Token savings ≈ **0%** — full schemas still reach the model. Validates that verify-only is not counted as a pruning win.

---

## RQ2 — Cost

**Question:** How much does CYT reduce **net monetary inference cost** after accounting for pruner cost?

### Net cost model

\[
\text{NetCost} = \text{LLMCost}_{agent} + \text{PrunerCost} + \text{InfrastructureCost}
\]

\[
\text{NetCostReduction} = 1 - \frac{\text{NetCost}_{CYT}}{\text{NetCost}_{baseline}}
\]

Do **not** report only "CYT reduced LLM cost by X%" without pruner cost.

### Report table

| Metric | Baseline | CYT | Δ |
|--------|----------|-----|---|
| Tool input tokens | | | |
| Total input tokens | | | |
| Output tokens | | | |
| Agent LLM cost | | | |
| Pruner cost (BM25=0, rerank/LLM>0) | | | |
| Total cost | | | |
| **Cost / successful task** | | | |

**Cost per success** is the strongest practical metric:

\[
\text{CostPerSuccess} = \frac{\text{TotalCost}}{\text{SuccessfulTasks}}
\]

A system 50% cheaper but 60% as successful may not be better.

### Codebase today

| Source | Coverage |
|--------|----------|
| `src/cyt/common/pricing.py` | `compute_stats_costs()`, `compute_net_savings_tokens()` |
| `src/tests/quality_metrics/test_pricing.py` | Unit tests for pricing math |
| BM25 pruner cost | **$0** (local Tantivy) |
| Rerank / LLM pruner | LiteLLM call costs tracked in stats stages |
| Task-linked cost | **Gap** — need harness |

---

## RQ3 — Task quality

**Question:** Does CYT preserve task completion accuracy when reducing tool context?

### Method (keep simple)

For each task:

1. Same user prompt
2. Same initial environment/state
3. Same tool catalog
4. Run **baseline** (full tools) and **CYT** (prune and/or verify)
5. Inspect resulting state
6. **Deterministic assertions** — not subjective LLM judgment

### Example

**Task:** "Create a GitHub issue titled 'Fix authentication bug' in repository qdrddr/example."

**Success assertions:**

- Issue exists
- `repository == qdrddr/example`
- `title == "Fix authentication bug"`

### Codebase today

| Source | Coverage |
|--------|----------|
| Gherkin features | Behavioral specs for gate/injection — not end-to-end task benchmark |
| `sdk/e2e/` | SDK smoke tests — not agent task eval |
| Deterministic task suite | **Not implemented** — primary eval build target |

---

## Supporting metrics (cross-cutting)

### Pruning quality (per task)

Gold required set \(G_t\), CYT selected set \(P_t\):

| Metric | Formula |
|--------|---------|
| Tool recall | \(\|G_t^{tools} \cap P_t^{tools}\| / \|G_t^{tools}\|\) |
| Property recall | \(\|G_t^{properties} \cap P_t^{properties}\| / \|G_t^{properties}\|\) |
| Enum recall | \(\|G_t^{enums} \cap P_t^{enums}\| / \|G_t^{enums}\|\) |

Separates "removed 90% of schema" from "retained 99.8% of task-relevant information."

### Verification quality

| Metric | Formula |
|--------|---------|
| Precision | CorrectlyAllowed / AllAllowed |
| Recall | CorrectlyBlocked / AllMalformed |
| False blocking rate (FBR) | ValidCallsBlocked / ValidCalls |

FBR is critical — false blocks directly harm task completion.

### Recovery (verify path)

| Metric | Definition |
|--------|------------|
| Recovery rate | MalformedTasksEventuallySuccessful / MalformedTasks |
| Recovery overhead | Extra model calls, tokens, latency after deny |

Target result shape: "CYT prevented X% malformed calls, recovered Y% of those tasks, average overhead Z tokens."

### Irrelevant-token removal (CYT-specific)

Report tools, properties, enums separately — see [paper-outline.md](./paper-outline.md).

---

## Statistical reporting

Paired experiment: same task, same seed where supported, N=5–10 repetitions.

Report: mean, median, std dev, 95% CI. Use paired tests when reusing the same task set.

Minimal eval scale (manageable v1):

- 50 tasks × 4 configs × 3 agents × 3 catalog sizes = 1,800 runs
- × 3 repetitions = 5,400 runs

Scale down to 5–10 tasks for harness validation first.
