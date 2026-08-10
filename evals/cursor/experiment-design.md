# Experiment design

**Do not run the full Cartesian product.** Three experiments only: primary (pruning), verification (MPR), smoke (harness validation).

See [evaluation-framework.md](./evaluation-framework.md) for configurations and levels.

---

## Primary experiment — Pruning vs baseline

**Compare:** No CYT **vs** CYT Pruning (verify off for cleanest pruning signal).

**Axes (v1):**

| Axis | Values |
|------|--------|
| Client | Cursor (hook) |
| Task type | Single-step, Multi-step |
| Catalog size | 25, 100, 250 |

**Primary metrics:** TaskSuccessRate, tool-context tokens, total input tokens, CostPerSuccess, required-tool recall.

**Cursor note:** Hook only; measure stub + rules-file injected tokens.

---

## Verification experiment

**Compare:** No CYT **vs** Verify only.

**Focus:** Level 1 — MPR on labeled schema-invalid corpus + verify-config agent runs.

**Expectation:** Token savings ≈ 0%; malformed-call prevention ↑.

**Setup:** `cyt hook cursor --prevent-hallucinations`

**Include:** Static labeled corpus (see [benchmark-design.md](./benchmark-design.md)).

---

## Smoke experiment (harness Phase 1)

5 tasks × 4 configurations — validate harness before scaling to 50 tasks.

---

## Pruning defaults

- **Pipeline:** BM25 only (`pruning.tools.sequence: [bm25]`)
- **Aggregator:** CYT-MCP only
- **Catalog threshold:** Default `minimum_tools: 50` skips pruning below threshold — override in eval config for size-25 runs

---

## Benchmark

**Layer B only** — CYT stress tasks in `evals/cursor/tasks/` (proposed). See [benchmark-design.md](./benchmark-design.md).

No external benchmark integration in v1.

---

## Randomized controlled protocol

Per (task, configuration):

- Same model, temperature, prompt, MCP servers, initial state, seed
- N = 3 repetitions (v1)
- Paired analysis across configurations

---

## v1 scope summary

| Dimension | v1 |
|-----------|-----|
| Primary | No CYT vs Pruning |
| Verification | No CYT vs Verify only (corpus + subset of tasks) |
| Client | Cursor |
| Tasks | 5 smoke → 50 Layer B |
| Configurations | 4-way ablation (smoke only); primary uses 2-way |
| Catalog sizes | 25, 100, 250 |
| Pipeline | BM25 |
| Aggregator | CYT-MCP |
| Repetitions | 3 |

---

## Headline figures

| Figure | X | Y | Lines |
|--------|---|---|-------|
| Fig 1 | Catalog size | Input tokens (tool-context + total) | Baseline, CYT |
| Fig 2 | Catalog size | TaskSuccessRate | Baseline, CYT |
| Fig 3 | Catalog size | CostPerSuccess | Baseline, CYT |

Optional: MPR by configuration (verification experiment).

---

## Follow-up (not v1)

- Claude/Codex via proxy (`stats.db` richer token data)
- BM25 vs rerank vs LLM pipeline comparison
- Hook vs proxy on Claude/Codex

---

## Environment checklist

```bash
uv tool install 'clear-your-tools[cyt-mcp,proxy,pruners]'
cyt hook cursor                          # pruning
cyt hook cursor --prevent-hallucinations   # verify only
```

Artifacts: `~/.config/cyt/sessions/*.jsonl`, `.cursor/rules/cyt-injection.mdc`.
