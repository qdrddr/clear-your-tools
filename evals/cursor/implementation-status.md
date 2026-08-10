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
| **CYT-MCP aggregator** | `src/cyt_mcp/` |
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

## Not implemented (eval gaps — in scope)

### P0 — Harness foundations

| Item | Notes |
|------|-------|
| **`evals/cursor/harness/`** | `run_task()`, per-call logging |
| **Four-level metric aggregation** | L1–L4 separate in results |
| **Tool-call record schema** | Every call classified A/B/C/D |
| **MPR** | Malformed-call prevention (verify configs) |
| **TaskSuccessRate** | Deterministic L4 verifiers — independent of tool metrics |
| **Deterministic task suite** | ~50 Layer B tasks with gold assertions |
| **Verification corpus runner** | Labeled JSONL → `tool_gate` / MPR |

### P1 — Token & cost (simplified)

| Item | Notes |
|------|-------|
| **Tool-context tokens** | stubs + injected definitions |
| **Total input / output tokens** | Per run aggregate |
| **Hook-path (Cursor) token accounting** | Rules file + session JSONL tokenization |
| **Cost per successful task** | Needs harness |
| **Required-tool recall** | Gold tool set vs pruned set per task |
| **Layer B CYT stress benchmark** | Distractors, schema bloat via catalog size |

---

## Deferred (removed from plan — low value / overcomplicated)

These were in earlier drafts but cut to keep the eval tractable. Revisit only if primary results are solid and time allows.

| Item | Why deferred |
|------|--------------|
| **Layer A external MCP benchmark** | Integration cost high; Layer B covers CYT-specific claims |
| **Turn-aware pruning ablation** | Partially implemented; hard to measure on Cursor; marginal vs primary results |
| **Compaction reinjection ablation** | Multi-turn setup complexity; niche mechanism |
| **Pre-exposure inject/skip metrics ablation** | Optimization detail; logic exists, aggregates not worth P0 |
| **Unexposed-but-allowed call rate** | Academic secondary; Type-2 admission is documented, not measured |
| **Schema-bloat isolation experiment** | Redundant with catalog-size sweep + distractor-heavy tasks |
| **Execution-unsuccessful corpus (Level 2)** | CYT Verify-Prevent doesn't address L2; measuring adds corpus work without validating CYT |
| **Recovery rate / overhead (Level 3)** | MPR covers verify value; multi-turn recovery tracking is complex |
| **TESR as headline metric** | Useful descriptively; not a CYT intervention metric |
| **Pipeline ablation (BM25 vs rerank vs LLM)** | v1 uses BM25 only; three pipelines × tasks × configs explodes matrix |
| **Catalog-size sweep 10–500** | v1 uses 25, 100, 250 only |
| **Cached vs uncached input token split** | Not in stats; Cursor opaque; note as limitation, use flat pricing for v1 |
| **Detailed input token breakdown** | user / agent-message / tool-schema / injected / other — partial on hook path |
| **Required-property / enum recall** | Gold annotation burden; required-tool recall sufficient |
| **Aggregator comparison matrix** | Use CYT-MCP only |
| **Hook vs proxy deployment comparison** | Cursor hook-only; defer to Claude/Codex follow-up |
| **Codex native vs Codex+CYT harness** | P2; validate ~20% claim separately |
| **Claude/Codex in v1 harness** | Cursor first; proxy `stats.db` is richer follow-up |
| **Figure generation pipeline** | After results exist |
| **Promotion to full definition tier** | Not implemented; verbatim dedup only |

---

## Partially implemented (no eval work planned)

| Item | Status |
|------|--------|
| **Turn-aware pruning** | Proxy: `extract_user_query`; not full latest-agent-message query |
| **Prompt cache preservation** | Stable stubs in `policies.py`; no cache split in stats |
| **Pre-exposed skip logic** | Works; no aggregate metrics planned for v1 |
| **Execution-unsuccessful (Level 2)** | MCP returns errors; not classified in CYT stats |

---

## Irrelevant (short)

| Item | Why |
|------|-----|
| Cursor proxy | Platform E2E encryption (`LIMITATIONS.md`) |
| Cloud-hosted proxy | Local-only |
| Copilot / OpenCode | Untested, out of scope |
| Full scenario Cartesian product | Do not run |
| LLM-as-judge task success | Use deterministic verifiers |
| Proving context rot formally | Frame as testing bloat reduction hypothesis only |
| `ui/` dashboard | Stub only |
