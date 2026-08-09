# Implementation status (adapted to this codebase)

Classification against **clear-your-tools** v2.11.x. See [evaluation-framework.md](./evaluation-framework.md) for the four-level model.

---

## Implemented

### Core CYT capabilities

| Capability | Code / docs |
|------------|-------------|
| **Three granularities:** tool → optional properties → enum values | `sdk/rust/cyt-indexer/src/pipeline/tools.rs`, `prune_enums` in `defaults.yaml` |
| **BM25 / Rerank / LLM pruning pipelines** | `src/cyt/pruners/{bm25,rerank,llm}.py` |
| **Verify-Prevent (Level 1 schema validation)** | `src/cyt_client/tool_gate.py`, `schema_validate.py` |
| **Schema-based admission (Type-2 catalog authority)** | `tool_gate.py` — not strict Type-1 exposure allowlist |
| **Deny + full schema for recovery (Level 3)** | `PreToolDenyExposure`, `session_pre_tool_exposure.py` |
| **Verify-only mode** | `--prevent-hallucinations`, `TOOL-HALLUCINATION-GATE.md` |
| **Stub + injected definition architecture** | `src/cyt_mcp/stubs.py` + `rules_file.py` / `user_message_inject.py` |
| **Pre-exposure / in-session promotion (skip re-inject)** | `src/cyt/injection/pre_exposed.py`, `pre_exposure_pipeline.py` |
| **Session compaction hook** | `preCompact` / `PreCompact` in `setup_wizard.py`, `test_session_compaction.py` |
| **Proxy + hook deployment** | `src/cyt/proxy/`, `src/cyt/hook/` |
| **Aggregators:** cyt-mcp, mcpc, executor, cloudflare | `src/cyt_mcp/`, `src/cyt/mcpc/`, `src/cyt/executor/`, `src/cyt/cloudflare/` |
| **Net cost w/ pruner stages** | `src/cyt/common/pricing.py`, `compute_net_savings_tokens()` |
| **Proxy token/property stats** | `src/cyt/proxy/stats.py` |
| **Pruning latency tests** | `src/tests/quality_metrics/test_pruning_timing.py` |

### Four configurations (manual)

All four ablation configs achievable via config + CLI — no batch runner yet.

### Terminology in code

| Research term | Code equivalent |
|---------------|-----------------|
| Schema-invalid | `validate_json_schema` failure, deny in `tool_gate` |
| Type-2 catalog | `tool_catalog:*` in session JSONL |
| Type-1 injection | Pruned/full tool entries in session log |
| In-session promotion | Pre-exposure filter (`is_pre_exposed`) |

---

## Not implemented (eval gaps)

### P0 — Harness foundations

| Item | Notes |
|------|-------|
| **`evals/cursor/harness/`** | `run_task()`, per-call logging |
| **Four-level metric aggregation** | L1–L4 separate in results |
| **Tool-call record schema** | Every call classified A/B/C/D |
| **MPR, TESR** | Malformed-call prevention, tool execution success |
| **TaskSuccessRate independent of tool metrics** | Deterministic verifiers |
| **Deterministic task suite** | 50+ tasks with gold assertions |

### P1 — Token & cost precision

| Item | Notes |
|------|-------|
| **Input token breakdown** | user / agent-message / tool-schema / injected / other |
| **Output token breakdown** | assistant / tool-call args |
| **Cached vs uncached input tokens** | Prompt-prefix cache modeling — stats use flat input price today |
| **Turn-aware pruning eval** | Proxy extracts user query; latest agent message not in prune query yet |
| **Hook-path (Cursor) token accounting** | Rules file + session JSONL tokenization |
| **Cost per successful task** | Needs harness |
| **In-session promotion metrics** | inject count, promotion count, tokens saved — logic exists, no aggregates |
| **Compaction reinjection ablation** | `preCompact` logged; no eval comparing before/after |
| **Pruning pipeline comparison harness** | BM25 vs rerank vs LLM on same tasks |
| **Layer A external MCP benchmark** | e.g. existing tool-use eval — not integrated |
| **Layer B CYT stress benchmark** | Schema bloat, distractors, semantic failures |
| **Unexposed-but-allowed call rate** | Schema admission secondary result |

### P2 — Paper artifacts

| Item | Notes |
|------|-------|
| Catalog-size sweep runner | 10–500 tools |
| Codex native vs Codex+CYT harness | `LIMITATIONS.md` ~20% claim — validate |
| Figure generation | Fig 1–3 from results parquet |
| Aggregator comparison matrix | Integration/reproducibility only |

---

## Partially implemented

| Item | Status |
|------|--------|
| **Turn-aware pruning** | Proxy: `extract_user_query` in `anthropic.py`; pre-exposure uses `combined_text` corpus — not full "latest agent message" as prune query |
| **Prompt cache preservation** | System tools + stubs kept stable (`policies.py`); no cached/uncached cost split in stats |
| **Recovery metrics** | Deny path exists; no recovery rate / overhead aggregates |
| **Execution-unsuccessful (Level 2)** | MCP returns errors; not classified in CYT stats |
| **Pre-exposed promotion → full definition** | Pre-exposure skips re-inject when verbatim in session; explicit "promote to full after N uses" — **not a separate promotion tier** |

---

## Irrelevant (short)

| Item | Why |
|------|-----|
| Cursor proxy | Platform E2E encryption (`LIMITATIONS.md`) |
| Cloud-hosted proxy | Local-only |
| Copilot / OpenCode | Untested, out of scope |
| Full scenario Cartesian product | Research says don't run it |
| LLM-as-judge task success | Use deterministic verifiers |
| Proving context rot formally | Frame as testing bloat reduction hypothesis only |
| `ui/` dashboard | Stub only |
