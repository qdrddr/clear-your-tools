# CYT capabilities (mapped to codebase)

Two complementary capabilities — **pruning** and **verification** — with shared stub architecture.

---

## CYT novelty (precise statement)

> CYT is an application for **dynamic reduction of tool context** presented to LLM agents.

It operates at three granularities:

```
Tool
 └── Properties (optional)
      └── Enum values
```

Most tool-selection systems answer: *Which tools are relevant?*

CYT additionally asks: *Which parts of the relevant tool schemas are relevant?*

Plus **schema-level verification** on tool calls (Verify-Prevent).

---

## Configuration matrix

### Three primary configurations

| Configuration | Pruning | Verify | Enable in repo |
|---------------|---------|--------|----------------|
| **No CYT** | ❌ | ❌ | Disable hooks/proxy |
| **CYT Verify-Prevent** | ❌ | ✅ | `cyt hook <agent> --prevent-hallucinations` |
| **CYT Pruning** | ✅ | Optional | `cyt hook cursor` or `cyt launch -- claude` |

Verify may be off inside pruning → four-way ablation (see [evaluation-framework.md](./evaluation-framework.md)).

---

## 1. Verify-Prevent (Level 1)

**Catches:** schema-invalid calls only.

| Validated | Not validated |
|-----------|---------------|
| Missing required, wrong type, bad enum | Wrong repo name, nonexistent resource |
| Unknown tool (not in Type-2 catalog) | Semantically invalid string values |

**Terminology:**

- **Schema-invalid** — violates declared schema
- **Execution-unsuccessful** — schema valid, backend failed
- **Semantically invalid** — valid type, wrong meaning

**Implementation:** `preToolUse` → `cyt-client` → `validate_pre_tool_call()` → `schema_validate.py`.

**Token savings:** ≈ 0% (full schemas visible; intervention only on malformed calls).

---

## 2. Tool-schema pruning

Pipeline: decompose → BM25/rerank/LLM score → filter → recompose.

| Pruner | Code | Cost |
|--------|------|------|
| BM25 (default) | `src/cyt/pruners/bm25.py` | $0 local |
| Rerank | `src/cyt/pruners/rerank.py` | DeepInfra via LiteLLM |
| LLM | `src/cyt/pruners/llm.py` | LiteLLM |

Config: `pruning.tools.sequence: [bm25]`; skip if catalog < `minimum_tools: 50`.

---

## Stub + injected definition (architecture)

CYT always follows:

```
full tool → pruned tool → stub (stable name) + injected definition (dynamic)
```

Model sees minimal stub on MCP wire:

```json
{ "name": "search_repository" }
```

Relevant schema injected via:

| Path | Injection |
|------|-----------|
| **Proxy** | User-message inject (`user_message_inject.py`) |
| **Hook / Cursor** | Rules file `.cursor/rules/cyt-injection.mdc` |
| **Hook / Claude/Codex** | `additionalContext` on hook response |

**Property:** Stable tool interface (prefix/cache-friendly) + dynamic contextual definitions.

**Code:** `src/cyt_mcp/stubs.py` ( `{}` schemas on wire), `get-tool-definitions` for on-demand lookup.

---

## Schema-based tool-call admission

Gate authority is **Type-2 catalog** (full backend), not Type-1 (what was injected this turn):

- Tool in Type-2 + valid args → **allow** (even if not injected)
- Tool not in Type-2 → **deny** (hallucinated name)
- Tool in Type-2 + invalid args → **deny** + return full definition

This is **schema-based admission**, not exposure enforcement. Documented in paper; rate measurement deferred.

**Code:** `src/cyt_client/tool_gate.py` line ~897; Type-2 built in `tool_catalog_emit.py`, `verify_session_log.py`.

---

## In-session tool promotion (pre-exposure)

After a tool definition is injected verbatim into session text, CYT **skips re-injecting** it:

```
1st exposure → inject relevant schema
2nd exposure → skip (already in session)
...
```

**Code:** `is_pre_exposed()` in `src/cyt/injection/pre_exposed.py`, used by `pre_exposure_pipeline.py` on hook and proxy paths.

**Gap:** Explicit "promote to full definition after N uses" tier — not implemented; current behavior is verbatim dedup. Pre-exposure ablation deferred from eval plan.

---

## Conversation compaction

On `preCompact` / `PreCompact` hook, session memory compresses → CYT resets pre-exposure assumptions → reinjects tools that were pre-exposed before compaction.

**Code:** `src/cyt/hook/setup_wizard.py` registers event; `src/tests/unit/test_session_compaction.py`.

Compaction ablation deferred from eval plan — see [implementation-status.md](./implementation-status.md).

---

## Turn-aware pruning

Pruning uses user query + session context. Latest agent message as primary BM25 query input is partially implemented on proxy path.

Turn-aware ablation deferred from eval plan.

---

## Deployment paths

| Agent | Proxy | Hook | Verify |
|-------|-------|------|--------|
| Claude | ✅ `cyt launch -- claude` | ✅ | ✅ |
| Codex | ✅ (+ native tool pruning) | ✅ | ✅ |
| Cursor | ❌ platform | ✅ required | ✅ |

### Prompt-prefix caching (proxy)

Stable prefix: system prompt + system tools + tool stubs.

CYT deliberately preserves system tools and stub names for cache hits (`policies.py`).

**Eval v1:** Flat input pricing; cache split deferred.

---

## Aggregator (v1 eval)

**CYT-MCP** (`src/cyt_mcp/`) — default for all eval runs. Other aggregators (mcpc, executor, cloudflare) exist but are not compared in v1.

---

## Context bloat framing (paper language)

> As available tools grow, the model sees more competing tool descriptions, parameter descriptions, and enumeration values. This increases context that must be processed and may dilute task-relevant information. We refer to this as **tool-context bloat** and relate it to the broader problem of **context rot**.

**Do not claim CYT proves context rot.** Claim: CYT tests whether reducing irrelevant tool context improves efficiency and preserves or improves task completion.

See `LIMITATIONS.md` for cited context-rot literature.
