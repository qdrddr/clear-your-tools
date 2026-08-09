# Implementation status (adapted to this codebase)

Classification of research recommendations against the current **clear-your-tools** repo (v2.11.x).

---

## Implemented

### Core CYT capabilities

| Capability | Code / docs |
|------------|-------------|
| **Tool-schema pruning** (tool → optional property → enum) | `sdk/rust/cyt-indexer/src/pipeline/tools.rs`, `src/cyt/pruners/tools_filter.py`, `prune_enums` in `defaults.yaml` |
| **BM25 pruner** (default, local, no API key) | `src/cyt/pruners/bm25.py` |
| **Rerank pruner** (DeepInfra via LiteLLM) | `src/cyt/pruners/rerank.py` |
| **LLM pruner** (XML selector via LiteLLM) | `src/cyt/pruners/llm.py` |
| **Pruning policies** (system vs MCP, optional/enum/description) | `src/cyt/pruners/policies.py` |
| **Required properties preserved** during prune | Rust pipeline + policy layer |
| **Verify-prevent (schema/shape validation)** | `src/cyt_client/tool_gate.py`, `src/cyt_client/schema_validate.py` |
| **Verify-only mode** (`--prevent-hallucinations`) | `TOOL-HALLUCINATION-GATE.md`, `hallucination_gate.enabled` |
| **Deny malformed calls + return full schema for recovery** | `PreToolDenyExposure` in `tool_gate.py`, `session_pre_tool_exposure.py` |
| **Syntactic validation only** (type, required, enum) — not semantic | Documented in gate; no repo-existence checks |

### Deployment modes

| Mode | Status | Notes |
|------|--------|-------|
| **Reverse proxy** (Claude, Codex) | `src/cyt/proxy/reverse.py` | Prune + inject in upstream request |
| **Hook + injection** (all agents) | `src/cyt/hook/`, `src/cyt/tools/hook.py` | Prune on prompt submit |
| **Cursor hook-only** (no proxy) | `LIMITATIONS.md`, `src/cyt/agents/cursor/` | Platform constraint, not missing feature |
| **cyt-mcp stub + on-demand schema** | `src/cyt_mcp/stubs.py`, `search.py` | Default MCP path for hooks |
| **Cursor rules-file injection** | `src/cyt_client/rules_file.py` | Workaround: Cursor ignores `additionalContext` on `beforeSubmitPrompt` |
| **Proxy user-message injection** | `src/cyt/proxy/user_message_inject.py` | Claude/Codex |
| **Per-agent `inject_via`** | `pruning.inject_via` in `defaults.yaml` | cursor→hook, claude/codex→proxy |
| **System tool preservation** (proxy) | Policy in `pruners/policies.py` | Default: system tools kept for cache prefix stability |

### Agents

| Agent | Proxy | Hook | Verify gate |
|-------|-------|------|-------------|
| Claude | ✅ | ✅ | ✅ |
| Codex | ✅ | ✅ | ✅ |
| Cursor | ❌ (platform) | ✅ | ✅ |

### Instrumentation (partial — see metrics doc)

| Metric area | Status |
|-------------|--------|
| Tool/property counts in/out (proxy) | `proxy_request` table in `src/cyt/proxy/stats.py` |
| Token counts (cl100k estimate) | `src/cyt/common/token_usage.py`, stats DB |
| Cost estimation (agent + rerank/LLM pruner) | `src/cyt/common/pricing.py` |
| Net savings after pruner cost | `compute_net_savings_tokens()` |
| Pruning timing | `src/tests/quality_metrics/test_pruning_timing.py` |
| Removed-chunks parity (Rust ↔ Python) | `src/tests/quality_metrics/test_removed_chunks.py` |
| Hallucination gate behavior (Gherkin) | `src/tests/unit/gherkin/features/hallucination_gate.feature` |
| BM25 token smoke (SDK e2e) | `sdk/e2e/python/tests/test_bm25_tokens_smoke.py` |

### Paper-relevant system description (already in repo)

- Architecture: proxy vs hook vs verify-only — `README.md`, `CURSOR-HOOK.md`, `CONFIG.md`
- Codex native pruning vs CYT additive savings — `LIMITATIONS.md` (~20% additional claim)
- Open-source artifact — PyPI `clear-your-tools`, public docs

---

## Not implemented (eval gaps to build)

| Item | Priority | Notes |
|------|----------|-------|
| **`evals/` harness** (`run_task()` API) | **P0** | No agent task benchmark exists |
| **Deterministic task suite** (50–100 tasks with gold assertions) | **P0** | Required for RQ3 |
| **Paired multi-run experiments** (N=5–10, CI, stats) | **P0** | Stochastic agent trajectories |
| **Four-way ablation runner** (baseline / verify-only / prune / full) | **P0** | Config switch exists; no batch runner |
| **Catalog-size sweep** (10–500 tools) | **P1** | Need synthetic distractor generator |
| **Pruning recall metrics** (tool/property/enum recall vs gold set G_t) | **P1** | Stats track counts, not task-relevant recall |
| **Verification benchmark dataset** (valid/malformed call corpus) | **P1** | Unit tests exist; no labeled eval set |
| **Recovery rate + recovery overhead** | **P1** | Gate denies + exposes schema; no aggregate recovery metric |
| **False blocking rate (FBR)** on verification | **P1** | Not aggregated |
| **Hook-path token accounting** (Cursor) | **P1** | Proxy stats rich; hook path thinner |
| **Per-task cost-per-success** | **P1** | Pricing helpers exist; not tied to task success |
| **Schema-bloat isolation experiment** (§18 in research) | **P2** | Synthetic tool with N irrelevant props/enums |
| **Codex+CYT vs Codex-native comparison harness** | **P2** | Documented claim; needs controlled repro |
| **Deployment-mode comparison matrix** (proxy vs hook per agent) | **P2** | Manual today |
| **Figure generation pipeline** | **P2** | For paper artifacts |
| **Proxy-side response tool-call rewrite** | — | README aspirational; actual verify is **preToolUse hook** |

---

## Irrelevant (for this repo / Cursor eval)

| Item | Reason |
|------|--------|
| Cursor reverse-proxy mode | Platform E2E encryption — documented in `LIMITATIONS.md` |
| Cloud-hosted agent proxy interception | Local-only by design |
| Copilot / OpenCode eval | Mentioned, untested — out of scope for v1 |
| `ui/` web dashboard | Stub only (`ui/Taskfile.yml`) |
| chunk-your-tools/skills git submodules | Optional; BM25 in `cyt-indexer` is self-contained |
| MCP SEP-2106 inputSchema migration | Backlog (`backlog/tasks/task-0002`) |
| Subjective LLM-as-judge task quality | Research recommends deterministic assertions instead |
| Building a new MCP spec | CYT consumes MCP; does not extend the protocol |
