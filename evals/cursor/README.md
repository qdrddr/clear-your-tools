# CYT Evaluation Research (Cursor focus)

Research notes for an arXiv draft on **Clear Your Tools (CYT)** and for building evaluation tests in this repo.

## Goals

1. Draft an arXiv article evaluating CYT.
2. Build evaluation tests measuring three **primary results**:
   - **Cost reduction** (net: agent + pruner, cache-aware)
   - **Token consumption** (input/output split; tool-context vs total)
   - **Task quality** (deterministic TaskSuccessRate — independent of tool-call metrics)

## Key conceptual change

**Separate tool-call correctness from task correctness.** Four evaluation levels (schema → execution → trajectory → task). See [evaluation-framework.md](./evaluation-framework.md).

## Configurations

| Config | Pruning | Verify |
|--------|---------|--------|
| No CYT | ❌ | ❌ |
| Verify only | ❌ | ✅ |
| Pruning only | ✅ | ❌ |
| Pruning + Verify | ✅ | ✅ |

## Files

| File | Contents |
|------|----------|
| [evaluation-framework.md](./evaluation-framework.md) | **Four levels, tool-call taxonomy, MPR/TESR, core narrative** |
| [implementation-status.md](./implementation-status.md) | Implemented / Not implemented / Irrelevant |
| [cyt-capabilities.md](./cyt-capabilities.md) | Modes, stubs, schema admission, deployment paths |
| [paper-outline.md](./paper-outline.md) | Abstract, novelty, context bloat framing |
| [research-questions.md](./research-questions.md) | Three primary results + supporting metrics |
| [experiment-design.md](./experiment-design.md) | Primary / verification / ablation experiments |
| [benchmark-design.md](./benchmark-design.md) | Layer A (external) + Layer B (CYT stress) |
| [metrics-instrumentation.md](./metrics-instrumentation.md) | Canonical metrics table ↔ codebase |
| [eval-harness-spec.md](./eval-harness-spec.md) | `run_task()` + per-call logging |
| [cursor-eval-focus.md](./cursor-eval-focus.md) | Cursor hook-only eval plan |

## Codebase anchors

| Area | Path |
|------|------|
| Pruning | `src/cyt/pruners/tools_filter.py`, `sdk/rust/cyt-indexer/` |
| Verify gate | `src/cyt_client/tool_gate.py` (Type-2 catalog authority) |
| Pre-exposure | `src/cyt/injection/pre_exposed.py`, `pre_exposure_pipeline.py` |
| Stubs + inject | `src/cyt_mcp/stubs.py`, `src/cyt_client/rules_file.py` |
| Compaction | `preCompact` hooks, `test_session_compaction.py` |
| Stats / pricing | `src/cyt/proxy/stats.py`, `src/cyt/common/pricing.py` |
| Cursor setup | `examples/agents/cursor/`, `CURSOR-HOOK.md` |

## Build order

1. [evaluation-framework.md](./evaluation-framework.md) + [implementation-status.md](./implementation-status.md)
2. Harness Phase 0: per-call logging + four configurations
3. 5 smoke tasks with deterministic verifiers (Levels 1–4)
4. Scale: primary experiment (No CYT vs Pruning) × Claude/Codex/Cursor × single/multi-step
