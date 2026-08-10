# Paper outline (arXiv draft)

Adapted for **clear-your-tools** with focused evaluation design. See [evaluation-framework.md](./evaluation-framework.md).

---

## Title

**Recommended:**

> Clear Your Tools: Dynamic Tool-Schema Pruning and Tool-Call Verification for Efficient LLM Agents

---

## Abstract (draft)

Large language model agents interact with external capabilities through tool-calling interfaces such as the Model Context
Protocol (MCP). As tool catalogs grow, exposing complete schemas increases input-token consumption, inference cost, and
competing parameter descriptions — **tool-context bloat** that may dilute task-relevant information (related to broader
**context rot**).

We present **Clear Your Tools (CYT)**, a client-side system that dynamically reduces tool context while preserving execution
capability. CYT operates at two levels: (1) **pruning** — query-dependent removal of irrelevant tools, optional properties,
and enum values while preserving required fields; (2) **verification** — intercepting schema-invalid tool calls before
execution and returning full definitions for recovery.

CYT deploys via **reverse proxy** (Claude, Codex) or **hook + stub injection** (Cursor). Stable tool stubs preserve
prompt-prefix caching; pruned definitions inject dynamically.

We evaluate along **three primary dimensions**: token efficiency (tool-context and total input), net monetary cost including
pruner overhead, and **task success** via deterministic verifiers — **independent** of tool-call metrics. We report
schema-level malformed-call prevention (MPR) for the verification configuration and required-tool recall for pruning.

**Artifact:** PyPI `clear-your-tools`, docs `CONFIG.md`, `CURSOR-HOOK.md`, `TOOL-HALLUCINATION-GATE.md`.

---

## Central hypothesis

> Dynamic removal of irrelevant tool schemas can substantially reduce LLM input-token consumption and net inference cost
> while preserving task completion accuracy; schema-level verification can prevent schema-invalid tool calls with limited
> overhead.

We **test** this — not assume it.

---

## CYT novelty (§Introduction / §4)

> CYT dynamically reduces tool context at three granularities: **tools → optional properties → enum values**.

Tool retrieval asks *which tools*; CYT asks *which parts of relevant schemas* matter, plus schema-level verification.

Differentiation from RAG-MCP, semantic tool discovery, MCP-Zero: **schema transformation**, not tool-level retrieval alone.

---

## Terminology (§3 / §Limitations)

| Term | Definition |
| ------ | ------------ |
| **Schema-invalid tool call** | Args violate declared schema (Verify-Prevent scope) |
| **Execution-unsuccessful** | Schema valid; backend failed |
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
   - 4.7 Recovery (deny + schema exposure)
   - 4.8 Pre-exposure & compaction (implementation; not ablated in v1 eval)
5. Deployment — proxy, hook, Cursor
6. Evaluation
   - 6.1 Four evaluation levels (L1 + L4 primary; L2–L3 descriptive)
   - 6.2 Configurations (none / verify / prune / full)
   - 6.3 Benchmark (Layer B stress tasks)
   - 6.4 Metrics ([research-questions.md](./research-questions.md) table)
   - 6.5 Experiments ([experiment-design.md](./experiment-design.md))
7. Results
   - 7.1 Tokens (tool-context + total)
   - 7.2 Net cost + CostPerSuccess
   - 7.3 TaskSuccessRate
   - 7.4 MPR (verification experiment)
   - 7.5 Required-tool recall
8. Related Work
9. Limitations — semantic validation, Cursor no-proxy, flat pricing in v1, BM25 languages
10. Discussion
11. Conclusion

---

## Core narrative diagram

```text
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
| ----- | --- | --- |
| 1 | Catalog size | Tool-context + total input tokens |
| 2 | Catalog size | TaskSuccessRate |
| 3 | Catalog size | CostPerSuccess |

Optional: MPR by configuration (verification experiment).

---

## Related work

| Work | CYT difference |
| ------ | ---------------- |
| RAG-MCP | Tool retrieval; CYT prunes schema internals |
| Semantic Tool Discovery | Selection; not property/enum minimization |
| MCP-Zero | Proactive toolchain; CYT is per-turn schema minimization + verify |

---

## Reproducibility

- Package version, `defaults.yaml`, eval harness (when built)
- Four configurations documented with exact CLI
- Layer B task YAML + verification corpus
