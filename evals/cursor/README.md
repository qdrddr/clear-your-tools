# CYT Evaluation Research (Cursor focus)

Research notes for an arXiv draft on **Clear Your Tools (CYT)** and for building evaluation tests in this repo.

## Goals

1. Draft an arXiv article evaluating CYT (tool-schema pruning + tool-call verification).
2. Build evaluation tests for the article that measure:
   - **Cost reduction** (net, including pruner cost)
   - **Token consumption reduction** (tool-schema vs total input)
   - **Task completion quality** — simple deterministic success/failure with and without CYT

## Scope

This directory focuses on **Cursor** as the primary eval target (hook-only path). Cross-agent comparisons (Claude proxy, Codex proxy+native pruning) are documented for the full paper but secondary for the first harness build.

## Files

| File | Contents |
|------|----------|
| [paper-outline.md](./paper-outline.md) | Title, abstract, structure, hypothesis, related work |
| [cyt-capabilities.md](./cyt-capabilities.md) | Two CYT modes, deployment paths mapped to this codebase |
| [research-questions.md](./research-questions.md) | RQ1–RQ3 with metric definitions |
| [experiment-design.md](./experiment-design.md) | Ablation matrix, catalog-size sweep, deployment modes |
| [benchmark-design.md](./benchmark-design.md) | Task categories, verification dataset, recovery eval |
| [metrics-instrumentation.md](./metrics-instrumentation.md) | Metrics ↔ existing code vs gaps to build |
| [eval-harness-spec.md](./eval-harness-spec.md) | `run_task()` interface and minimal first build |
| [implementation-status.md](./implementation-status.md) | **Implemented / Not implemented / Irrelevant** |
| [cursor-eval-focus.md](./cursor-eval-focus.md) | Cursor-specific constraints and eval plan |

## Codebase anchors

| Area | Path |
|------|------|
| Pruning pipeline | `src/cyt/pruners/tools_filter.py`, `sdk/rust/cyt-indexer/src/pipeline/tools.rs` |
| Verify-only gate | `src/cyt_client/tool_gate.py`, `TOOL-HALLUCINATION-GATE.md` |
| Proxy path | `src/cyt/proxy/reverse.py`, `src/cyt/proxy/anthropic.py` |
| Hook path | `src/cyt/hook/`, `src/cyt_client/cli.py`, `src/cyt_client/rules_file.py` |
| cyt-mcp stubs | `src/cyt_mcp/stubs.py`, `src/cyt_mcp/search.py` |
| Stats / pricing | `src/cyt/proxy/stats.py`, `src/cyt/common/pricing.py` |
| Existing quality tests | `src/tests/quality_metrics/`, `src/tests/unit/gherkin/` |
| Cursor setup | `examples/agents/cursor/`, `CURSOR-HOOK.md` |

## Suggested build order

1. Read [implementation-status.md](./implementation-status.md) and [eval-harness-spec.md](./eval-harness-spec.md).
2. Implement a minimal harness under `evals/cursor/harness/` (not yet present).
3. Start with 5–10 deterministic tasks + 4 configurations (baseline, verify-only, prune, full).
4. Expand to 50 tasks × 3 catalog sizes before writing Results sections.
