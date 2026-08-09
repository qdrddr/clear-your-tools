# Experiment design

Experimental matrix, ablations, and controlled sweeps adapted to this codebase.

---

## Four-way ablation (required)

| Configuration | Pruning | Verification | Repo setup |
|---------------|---------|--------------|------------|
| **Baseline** | ❌ | ❌ | Agent MCP config without CYT; or CYT fully disabled |
| **Verify-only** | ❌ | ✅ | `cyt hook cursor --prevent-hallucinations` |
| **CYT-prune** | ✅ | ❌ | `cyt hook cursor` with `hallucination_gate.enabled: false` |
| **CYT-full** | ✅ | ✅ | Pruning + `hallucination_gate.enabled: true` |

### Expected outcomes

| Config | Token savings | Task errors |
|--------|---------------|-------------|
| Baseline | 0% (reference) | Baseline malformed-call rate |
| Verify-only | ≈ 0% | ↓ malformed calls; possible recovery overhead |
| CYT-prune | High tool-schema reduction | Risk if recall < 1.0 |
| CYT-full | High + verify safety net | Best combined |

---

## Agent × deployment matrix

### Cursor (primary eval target)

| Condition | Available? | Setup |
|-----------|------------|-------|
| Full tools | ✅ | Native MCP, no CYT |
| CYT hook (prune) | ✅ | `cyt hook cursor` + cyt-mcp |
| CYT hook (verify-only) | ✅ | `--prevent-hallucinations` |
| CYT proxy | ❌ | Platform limitation (`LIMITATIONS.md`) |

### Claude

| Condition | Setup |
|-----------|-------|
| Full tools | Direct API / no proxy |
| CYT proxy | `cyt launch -- claude` |
| CYT hook | `pruning.inject_via.claude: hook` |
| Verify-only | `--prevent-hallucinations` |

### Codex

| Condition | Setup |
|-----------|-------|
| Full tools | Direct |
| Codex native pruning | Default Codex behavior |
| CYT proxy | `cyt launch -- codex` |
| Codex + CYT | Proxy or hook + native pruning |
| Verify-only | `--prevent-hallucinations` |

**Paper metric for Codex:**

\[
\text{AdditionalReduction} = 1 - \frac{\text{Tokens}_{Codex+CYT}}{\text{Tokens}_{Codex}}
\]

Hypothesis from `LIMITATIONS.md`: ~20% additional savings — **validate experimentally**, do not cite as established result.

---

## Catalog size sweep (controlled experiment)

Same task set, vary distractor tool count:

| Catalog size | Purpose |
|--------------|---------|
| 10 | Below CYT `minimum_tools: 50` — pruning may skip |
| 25 | Small catalog |
| 50 | At default threshold |
| 100 | Medium |
| 200 | Large |
| 500 | Stress test |

### Build approach

1. **Real tools:** 5–10 MCP backends via `~/.config/cyt/mcp/<agent>.json` (GitHub, filesystem, etc.)
2. **Synthetic distractors:** Generate fake tool definitions with plausible names/schemas
3. **Gold tool chains:** Per-task annotation of required tools/properties/enums

### Note on `minimum_tools: 50`

Default policy in `defaults.yaml` skips pruning below 50 tools. For catalog-size experiment:

- Either document "no prune" behavior at 10/25 as a finding
- Or override `pruning.tools.policy.minimum_tools` in eval config

---

## Schema-bloat isolation experiment (§18)

Hold **tool count** and **task** constant; inflate one tool's schema:

| Condition | Irrelevant schema added |
|-----------|-------------------------|
| Baseline | 0 |
| Bloat-10 | 10 irrelevant optional properties |
| Bloat-50 | 50 |
| Bloat-100 | 100 |
| Bloat-500enums | 500 irrelevant enum values |

**Expected:** Baseline tokens ↑, CYT tokens ≈ flat.

**Implementation:** Synthetic MCP server or fixture catalog in harness — **not yet in repo**.

---

## Randomized controlled evaluation protocol

For every (task, configuration) pair:

| Control | Value |
|---------|-------|
| Model | Fixed per agent (e.g. Claude Sonnet, GPT-4.1) |
| Temperature | Fixed |
| Prompt | Identical |
| MCP servers | Identical |
| Initial state | Identical (fresh repo/issue sandbox) |
| Random seed | Fixed where agent supports it |
| Repetitions | N = 5–10 |

### Paired analysis

Same task IDs across baseline and CYT → paired statistical tests.

---

## Minimal v1 scope (recommended first build)

| Dimension | v1 value |
|-----------|----------|
| Tasks | 50 (start with 5–10 for harness debug) |
| Configurations | 4 (ablation matrix) |
| Agents | Cursor first; Claude/Codex later |
| Catalog sizes | 25, 100, 250 |
| Repetitions | 3 |

**Runs:** 50 × 4 × 1 × 3 × 3 = 1,800 (Cursor only)

Add Claude/Codex for full paper: 50 × 4 × 3 × 3 × 3 = 5,400 (+ repetitions).

---

## Environment setup checklist

```bash
uv tool install 'clear-your-tools[cyt-mcp,proxy,pruners]'
cyt hook cursor                    # or --prevent-hallucinations
# Configure backends: ~/.config/cyt/mcp/cursor.json
# Aggregator: ~/.config/cyt/mcp-aggregator.yaml
```

Session artifacts for analysis:

- `~/.config/cyt/stats.db` — proxy stats (Claude/Codex)
- `~/.config/cyt/sessions/*.jsonl` — Type-1/Type-2 catalogs, exposure on deny
- `.cursor/rules/cyt-injection.mdc` — injected pruned schemas (Cursor)

---

## Outputs per run (target)

See [eval-harness-spec.md](./eval-harness-spec.md) for the `run_task()` return schema.

Aggregate into CSV/Parquet for figure generation (Fig 1–3).
