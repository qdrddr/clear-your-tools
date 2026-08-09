# Experiment design

**Do not run the full Cartesian product.** Designate primary, verification, and ablation experiments.

See [evaluation-framework.md](./evaluation-framework.md) for configurations and levels.

---

## Scenario dimensions (reference matrix)

| Dimension | Values |
|-----------|--------|
| Task | Single-step / Multi-step |
| Client | Claude / Codex / Cursor |
| CYT | None / Verify / Prune / Prune+Verify |
| Injection | Hook / Proxy |
| Aggregator | Cloudflare / Executor / mcpc / CYT-MCP |
| Pipeline | BM25 / Rerank / LLM |
| Catalog size | Small / Medium / Large |
| Session state | New / Pre-exposed / Compacted |

---

## Primary experiment — Pruning vs baseline

**Compare:** No CYT **vs** CYT Pruning (verify optional off for cleanest pruning signal; report Prune+Verify separately).

**Across:**

| Axis | Values |
|------|--------|
| Client | Claude, Codex, Cursor |
| Task type | Single-step, Multi-step |
| Catalog size | Small (25), Medium (100), Large (250+) |

**Primary metrics:** TaskSuccessRate, tool-context tokens, total input tokens, CostPerSuccess.

**Cursor note:** Hook only; measure stub + rules-file injected tokens.

---

## Verification experiment

**Compare:** No CYT **vs** Verify only.

**Focus:** Level 1–3 metrics — MPR, TESR, recovery rate, recovery overhead.

**Expectation:** Token savings ≈ 0%; malformed-call prevention ↑.

**Setup:** `cyt hook <agent> --prevent-hallucinations`

**Include:** Labeled schema-invalid corpus (see [benchmark-design.md](./benchmark-design.md)).

---

## Ablation experiments (run separately)

| Ablation | Question |
|----------|----------|
| **BM25 vs Rerank vs LLM** | Benefit from schema transformation vs sophisticated pruner? |
| **Hook vs Proxy** | Same task, Claude/Codex only (Cursor = hook only) |
| **Verify on vs off** (within pruning) | Pruning only vs Pruning + Verify |
| **Pre-exposed vs new session** | Does pre-exposure skip save tokens without hurting success? |
| **Before vs after compaction** | Does reinjection after `preCompact` preserve tool availability? |
| **Turn-aware query** | Prune on user prompt only vs user + latest agent message |
| **Catalog size sweep** | 10, 25, 50, 100, 200, 500 tools |
| **Schema-bloat isolation** | Constant tool count; inflate irrelevant props/enums |

### Catalog size note

Default `minimum_tools: 50` skips pruning below threshold — document or override in eval config.

---

## Pruning pipeline experiment

Clean independent axis — **not mixed into primary comparison**:

| Pipeline | Config |
|----------|--------|
| BM25 | `pruning.tools.sequence: [bm25]` |
| Rerank | `[bm25, rerank]` or `[rerank]` |
| LLM | `[llm]` |

Measure per pipeline: required-tool recall, token reduction, latency, \(C_{pruning}\), TaskSuccessRate.

**Code:** `src/cyt/pruners/tools_filter.py` orchestrates sequence from config.

---

## Aggregator experiment (reproducibility only)

**Not a primary statistical axis.**

| Client | Injection | Aggregator |
|--------|-----------|------------|
| Claude | Hook | Cloudflare, Executor, mcpc, CYT-MCP |
| Codex | Hook | … |
| Cursor | Hook | CYT-MCP (default), others as configured |
| Claude/Codex | Proxy | N/A (direct catalog) |

Document setup paths; one aggregator per primary run (recommend **CYT-MCP**).

**Code:** `tools_from` in `defaults.yaml`; `src/cyt_mcp/backends.py`.

---

## Codex-specific comparison

| Config | Purpose |
|------|---------|
| Codex full tools | Baseline |
| Codex native pruning | Harness default |
| Codex + CYT | Additional reduction |

\[
\text{AdditionalReduction} = 1 - T_{Codex+CYT} / T_{Codex}
\]

Validate `LIMITATIONS.md` ~20% claim — hypothesis, not established.

---

## Deployment mode comparison (Claude/Codex)

| Condition | Path |
|-----------|------|
| Full tools | No CYT |
| CYT proxy | `cyt launch --` |
| CYT hook | `inject_via: hook` |

Cursor: hook only — include in paper as constraint, not missing eval.

---

## Two benchmark layers

### Layer A — Existing MCP / tool-use benchmark

Run CYT against an **externally recognizable** benchmark.

- Less benchmark-construction bias
- Reproducibility for reviewers
- **Gap:** Not integrated — pick benchmark and wrap with harness

### Layer B — CYT-specific stress benchmark

Controlled tasks for mechanisms existing benchmarks won't isolate:

- Tool-schema bloat (props/enums)
- Distractor-heavy catalogs
- Schema-invalid calls (Verify)
- Execution-unsuccessful / semantic failures (Level 2)
- Repeated in-session tool use (pre-exposure)
- Conversation compaction

**Build in:** `evals/cursor/tasks/` (proposed).

---

## Randomized controlled protocol

Per (task, configuration):

- Same model, temperature, prompt, MCP servers, initial state, seed
- N = 5–10 repetitions
- Paired analysis across configurations

---

## Minimal v1 scope

| Dimension | v1 |
|-----------|-----|
| Primary | No CYT vs Pruning |
| Verification | No CYT vs Verify only (subset of tasks) |
| Client | Cursor first |
| Tasks | 5 smoke → 50 |
| Configurations | 4-way ablation |
| Catalog sizes | 25, 100, 250 |
| Pipeline | BM25 only |
| Aggregator | CYT-MCP |

---

## Headline figures

| Figure | X | Y | Lines |
|--------|---|---|-------|
| Fig 1 | Catalog size | Input tokens (tool-context + total) | Baseline, CYT |
| Fig 2 | Catalog size | TaskSuccessRate | Baseline, CYT |
| Fig 3 | Catalog size | CostPerSuccess | Baseline, CYT |

Optional: MPR vs catalog size (verification experiment).

---

## Environment checklist

```bash
uv tool install 'clear-your-tools[cyt-mcp,proxy,pruners]'
cyt hook cursor                          # pruning
cyt hook cursor --prevent-hallucinations   # verify only
```

Artifacts: `~/.config/cyt/sessions/*.jsonl`, `stats.db` (proxy), `.cursor/rules/cyt-injection.mdc`.
