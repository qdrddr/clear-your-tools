# Paper outline (arXiv draft)

Adapted for **clear-your-tools** with sharpened evaluation design. See [evaluation-framework.md](./evaluation-framework.md).

---

## Title

**Recommended:**

> Clear Your Tools: Dynamic Tool-Schema Pruning and Tool-Call Verification for Efficient LLM Agents

---

## Abstract (draft)

Large language model agents interact with external capabilities through tool-calling interfaces such as the Model Context Protocol (MCP). As tool catalogs grow, exposing complete schemas increases input-token consumption, inference cost, and competing parameter descriptions — **tool-context bloat** that may dilute task-relevant information (related to broader **context rot**).

We present **Clear Your Tools (CYT)**, a client-side system that dynamically reduces tool context while preserving execution capability. CYT operates at two levels: (1) **pruning** — query-dependent removal of irrelevant tools, optional properties, and enum values while preserving required fields; (2) **verification** — intercepting schema-invalid tool calls before execution and returning full definitions for recovery.

CYT deploys via **reverse proxy** (Claude, Codex) or **hook + stub injection** (Cursor). Stable tool stubs preserve prompt-prefix caching; pruned definitions inject dynamically.

We evaluate along **three primary dimensions**: token efficiency (tool-context and total input, with cache-aware cost), net monetary cost including pruner overhead, and **task success** via deterministic verifiers — **independent** of tool-call metrics. We separately report a four-level hierarchy: schema correctness, execution success, agent recovery, and task outcome. We measure malformed-call prevention, tool execution success, pruning recall, in-session pre-exposure, and compaction-aware reinjection.

**Artifact:** PyPI `clear-your-tools`, docs `CONFIG.md`, `CURSOR-HOOK.md`, `TOOL-HALLUCINATION-GATE.md`.

---

## Central hypothesis

> Dynamic removal of irrelevant tool schemas can substantially reduce LLM input-token consumption and net inference cost while preserving task completion accuracy; schema-level verification can prevent and recover from schema-invalid tool calls with limited overhead.

We **test** this — not assume it.

---

## CYT novelty (§Introduction / §4)

> CYT dynamically reduces tool context at three granularities: **tools → optional properties → enum values**.

Tool retrieval asks *which tools*; CYT asks *which parts of relevant schemas* matter, plus schema-level verification.

Differentiation from RAG-MCP, semantic tool discovery, MCP-Zero: **schema transformation**, not tool-level retrieval alone.

---

## Terminology (§3 / §Limitations)

| Term | Definition |
|------|------------|
| **Schema-invalid tool call** | Args violate declared schema (Verify-Prevent scope) |
| **Execution-unsuccessful** | Schema valid; backend failed |
| **Semantically invalid** | Valid schema type; wrong value (e.g. path vs `owner/repo`) |
| **Schema-based admission** | Validate against Type-2 catalog; allow valid calls even if not injected |
| **Tool-context bloat** | Growth of competing tool metadata with catalog size |

Avoid "incorrect data" for Level 2 failures.

---

## Paper structure

1. Introduction — bloat → CYT → three primary results
2. Background — LLM tools, MCP, tool-context bloat / context rot (cite carefully)
3. Problem — tool/property/enum redundancy; schema-invalid vs execution-unsuccessful
4. Clear Your Tools
   - 4.1 Architecture (stub + inject)
   - 4.2–4.4 Pruning granularities
   - 4.5 Schema reconstruction
   - 4.6 Verification (Type-2 admission)
   - 4.7 Recovery
   - 4.8 Pre-exposure & compaction
5. Deployment — proxy, hook, Cursor, Codex native pruning
6. Evaluation
   - 6.1 Four evaluation levels
   - 6.2 Configurations (none / verify / prune / full)
   - 6.3 Benchmarks (Layer A external + Layer B stress)
   - 6.4 Metrics ([research-questions.md](./research-questions.md) table)
   - 6.5 Experiments ([experiment-design.md](./experiment-design.md))
7. Results
   - 7.1 Tokens (tool-context + total; cached/uncached)
   - 7.2 Net cost + CostPerSuccess
   - 7.3 TaskSuccessRate
   - 7.4 Tool-call metrics (MPR, TESR)
   - 7.5 Pruning recall + pipeline ablation
   - 7.6 Recovery
   - 7.7 Codex comparison
   - 7.8 Pre-exposure & compaction
   - 7.9 Unexposed-but-allowed (secondary)
8. Related Work
9. Limitations — semantic validation, Cursor no-proxy, cache tradeoffs, BM25 languages
10. Discussion
11. Conclusion

---

## Core narrative diagram

```
         MCP / Tool-rich agents
                  │
                  ▼
          Tool-context bloat
                  │
     ┌────────────┴────────────┐
     │                         │
Cost / tokens            Context dilution
     │                         │
     └────────────┬────────────┘
                  ▼
            Clear Your Tools
                  │
     ┌────────────┴────────────┐
     │                         │
  Pruning                 Verification
  tools/properties/enums   schema-invalid calls
     │                         │
     ▼                         ▼
Smaller context          Prevent + recover
     │
     ▼
Lower input cost + preserved quality
```

**Do not claim CYT proves context rot.**

---

## Headline figures

| Fig | X | Y |
|-----|---|---|
| 1 | Catalog size | Tool-context + total input tokens |
| 2 | Catalog size | TaskSuccessRate |
| 3 | Catalog size | CostPerSuccess |

Optional: MPR by configuration; pipeline ablation bar chart.

---

## Related work

| Work | CYT difference |
|------|----------------|
| RAG-MCP | Tool retrieval; CYT prunes schema internals |
| Semantic Tool Discovery | Selection; not property/enum minimization |
| MCP-Zero | Proactive toolchain; CYT is per-turn schema minimization + verify |

---

## Reproducibility

- Package version, `defaults.yaml`, eval harness (when built)
- Four configurations documented with exact CLI
- Layer A benchmark citation + Layer B task YAML
