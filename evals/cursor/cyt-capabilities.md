# CYT capabilities (mapped to this codebase)

CYT operates as **two distinct capabilities**. Treat them separately in experiments and the paper.

---

## 1. Tool-call verification / hallucination prevention

**Purpose:** Block malformed tool calls (wrong shape) before execution; return full schema so the agent can recover on the next turn.

### What it catches (syntactic / schema validity)

Implemented in `src/cyt_client/schema_validate.py` + `tool_gate.py`:

- Missing required properties
- Wrong JSON types
- Invalid enum values
- Unknown tools (not in session Type-2 catalog)
- Additional properties (when schema disallows)

### What it does **not** catch (semantic validity)

- Correct type but wrong value (e.g. full path instead of `owner/repo`)
- Valid string for non-existent resource (e.g. repo does not exist)

Formalize in the paper:

- \(V_{schema}(a, S) \in \{0,1\}\) — CYT addresses this
- \(V_{semantic}(a, E) \in \{0,1\}\) — out of scope

### Behavior in verify-only mode

| Property | Value |
|----------|-------|
| Full MCP schemas visible to model | Yes — no pruning, no token savings |
| Intervention point | `preToolUse` hook only |
| Pruning disabled | `hallucination_gate.enabled: true` disables tools/skills injection |
| cyt-mcp mode | `verify_only: true` — no stub transform, no search tool |
| Entry | `cyt hook <agent> --prevent-hallucinations` |

**Code:** `TOOL-HALLUCINATION-GATE.md`, `src/cyt/injection/verify_session_log.py`, `src/cyt/proxy/verify_session_log.py` (proxy catalog writer when verify-only on Claude/Codex).

**Expected token savings in verify-only:** ≈ 0% (by design).

---

## 2. Tool-schema pruning

**Purpose:** Reduce tool metadata exposed to the model by removing irrelevant tools, optional properties, and enum values.

### Pruning pipeline

```
Tool discovery → decompose → score (BM25/rerank/LLM) → filter → recompose
```

| Stage | Implementation |
|-------|----------------|
| Decompose | `sdk/rust/cyt-indexer` — splits schemas into searchable chunks |
| Score | `src/cyt/pruners/bm25.py` (default), `rerank.py`, `llm.py` |
| Enum pruning | Rust BM25 + `prune_enums: true` |
| Recompose | `sdk/rust/cyt-indexer/src/retrieve.rs` |
| Skip below threshold | `minimum_tools: 50` in policy — no prune if catalog small |

Required properties on surviving tools are **always preserved**.

### Deployment paths for pruning

#### A. Reverse proxy (Claude, Codex)

```
Agent → cyt proxy:8834 → prune tools in request body → upstream LLM
```

- Mutates tool definitions in Anthropic/OpenAI request payload
- Can preserve system tools for prompt-cache prefix stability
- Injects pruned schemas via user message (`user_message_inject.py`)
- **Code:** `src/cyt/proxy/reverse.py`, `anthropic.py`, `openai_responses.py`

#### B. Hook + injection (Cursor **required**; optional for Claude/Codex)

```
Agent → cyt-mcp stubs (minimal {}) on wire
      → CYT hook prunes on beforeSubmitPrompt
      → pruned schema injected into agent context
```

| Agent | Injection mechanism | Code |
|-------|---------------------|------|
| Cursor | `.cursor/rules/cyt-injection.mdc` rules file | `src/cyt_client/rules_file.py` |
| Claude/Codex (hook) | `additionalContext` on hook response | `src/cyt/tools/hook.py` |
| All | cyt-mcp name-only stubs | `src/cyt_mcp/stubs.py` |

**Cursor limitation:** Native `additionalContext` on `beforeSubmitPrompt` is not delivered by Cursor IDE today — rules file is the production path (`examples/agents/cursor/README.MD`, `CURSOR-HOOK.md`).

#### C. Cursor cannot use proxy

Documented in `LIMITATIONS.md` — E2E encrypted traffic; not a CYT gap.

### Codex interaction

- Codex has **built-in tool pruning** (removes unused tools)
- CYT still adds value by pruning **optional properties and enums** (`LIMITATIONS.md`: ~20% additional savings when combined)
- Paper should report **Codex**, **Codex+CYT**, not assume 85% vs 50% without measurement

---

## Experiment configurations (ablation)

| Config | Pruning | Verification | How to enable in this repo |
|--------|---------|--------------|----------------------------|
| **Baseline** | ❌ | ❌ | Disable CYT hooks/proxy; full MCP catalog |
| **Verify-only** | ❌ | ✅ | `cyt hook cursor --prevent-hallucinations` |
| **CYT-prune** | ✅ | ❌ | `cyt hook cursor` (default pruning, gate off) |
| **CYT-full** | ✅ | ✅ | Pruning + `hallucination_gate.enabled: true` in config |

For Claude/Codex proxy equivalents: `cyt launch -- claude|codex` with config overlays.

---

## Architecture (for paper §4)

```
                    ┌─────────────────────────┐
                    │   LLM Agent             │
                    │ Claude / Codex / Cursor │
                    └────────────┬────────────┘
                                 │
                   ┌─────────────┴──────────────┐
                   │            CYT              │
                   │  1. Tool discovery         │
                   │  2. Query-aware pruning    │
                   │  3. Schema reconstruction  │
                   │  4. Tool-call verification │
                   └─────────────┬──────────────┘
                                 │
                         ┌───────┴───────┐
                         │  MCP servers  │
                         │ (via cyt-mcp) │
                         └───────────────┘
```

Three integration paths:

1. **Reverse proxy** — `src/cyt/proxy/`
2. **Hook/injection** — `src/cyt/hook/` + `src/cyt_client/`
3. **Verification** — `preToolUse` → `cyt-client` → `tool_gate.py`
