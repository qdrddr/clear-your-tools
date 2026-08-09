# Paper outline (arXiv draft)

Adapted for the **clear-your-tools** open-source artifact.

---

## Title options

**Recommended (systems / OSS):**

> Clear Your Tools: Dynamic Tool-Schema Pruning and Tool-Call Verification for Efficient LLM Agents

**Alternative (more academic):**

> Reducing Tool-Context Overhead in LLM Agents through Dynamic Tool-Schema Pruning and Verification

---

## Abstract (draft)

Large language model agents increasingly interact with external capabilities through tool-calling interfaces such as the Model Context Protocol (MCP). As the number of available tools grows, exposing complete tool catalogs to an agent can substantially increase input-token consumption and inference cost while increasing the number of competing tool and parameter descriptions presented to the model.

We present **Clear Your Tools (CYT)**, a client-side tool management system that dynamically reduces the tool context available to an LLM agent while preserving the ability to execute the underlying tools. CYT operates at two complementary levels. First, its **pruning mechanism** selects tools relevant to the current task and removes irrelevant optional properties and enumeration values from surviving tool schemas; required properties are preserved. Second, its **tool-call verification mechanism** intercepts tool invocations and prevents malformed calls whose arguments violate the exposed schema, returning the complete tool definition to the agent to enable recovery on a subsequent step.

CYT supports two deployment paths. In a **reverse-proxy** configuration, CYT intercepts model requests and dynamically rewrites tool definitions before they are sent to the model (Claude Code, Codex). In a **hook-based** configuration, CYT exposes lightweight tool stubs while injecting the relevant pruned definitions into the agent context via rules files (Cursor) or additional context (Claude/Codex hooks). The latter enables integration with environments that do not support an LLM reverse proxy. CYT can also operate solely as a verification hook when tool pruning is disabled (`--prevent-hallucinations`).

We evaluate CYT along three dimensions: token efficiency, monetary cost, and task completion quality. The evaluation compares agents operating with complete tool catalogs against agents using CYT pruning and/or verification. We measure both tool-schema token reduction and total request-token reduction, distinguish input-token savings from total task cost, and evaluate task success using deterministic task-specific assertions. We additionally measure the rate at which pruning removes required capabilities and the rate at which verification prevents malformed tool calls.

Our evaluation investigates whether dynamically reducing tool-context size can substantially lower agent operating costs while preserving task completion reliability, and whether schema-level verification can recover from malformed tool calls without requiring the model to be exposed to the complete tool catalog on every request.

**Reproducibility artifact:** PyPI package `clear-your-tools`, config in `~/.config/cyt/`, docs in repo root (`CONFIG.md`, `CURSOR-HOOK.md`, `TOOL-HALLUCINATION-GATE.md`).

---

## Central hypothesis

> Dynamic removal of irrelevant tool schemas can substantially reduce LLM input-token consumption and inference cost while preserving task completion accuracy, and schema-level verification can prevent or recover from malformed tool calls with limited additional overhead.

The paper **tests** this hypothesis; it does not assume CYT works.

---

## Paper structure

1. **Introduction**
2. **Background**
   - 2.1 LLM tool calling
   - 2.2 MCP (`src/cyt_mcp/`, cyt-mcp aggregator)
   - 2.3 Tool-context overhead (cite RAG-MCP, semantic tool discovery, MCP-Zero)
3. **Problem Definition**
   - 3.1 Tool-level redundancy
   - 3.2 Property-level redundancy
   - 3.3 Enum-level redundancy
   - 3.4 Malformed tool calls (shape vs semantic — see `cyt-capabilities.md`)
4. **Clear Your Tools**
   - 4.1 Architecture
   - 4.2 Tool pruning (`tools_filter.py`, Rust pipeline)
   - 4.3 Property pruning
   - 4.4 Enum pruning (`prune_enums`)
   - 4.5 Schema reconstruction
   - 4.6 Tool-call verification (`tool_gate.py`)
   - 4.7 Recovery mechanism (`PreToolDenyExposure`)
5. **Deployment Modes**
   - 5.1 Reverse proxy (`src/cyt/proxy/`)
   - 5.2 Hook/injection (`src/cyt/hook/`, `cyt-client`)
   - 5.3 Cursor integration (`rules_file.py`, cyt-mcp stubs)
   - 5.4 Codex integration (+ native pruning baseline)
   - 5.5 Claude integration
6. **Evaluation**
   - 6.1 Research questions → [research-questions.md](./research-questions.md)
   - 6.2 Benchmark → [benchmark-design.md](./benchmark-design.md)
   - 6.3 Experimental setup → [experiment-design.md](./experiment-design.md)
   - 6.4 Metrics → [metrics-instrumentation.md](./metrics-instrumentation.md)
   - 6.5 Baselines (4-way ablation)
7. **Results**
   - 7.1 Token reduction
   - 7.2 Cost reduction (net, incl. pruner)
   - 7.3 Task completion
   - 7.4 Pruning recall
   - 7.5 Verification accuracy
   - 7.6 Recovery
   - 7.7 Codex native pruning comparison
   - 7.8 Ablation study
8. **Related Work** (below)
9. **Limitations** (`LIMITATIONS.md`: semantic validation, Cursor proxy, cache invalidation, BM25 language)
10. **Discussion**
11. **Conclusion**

---

## Headline figures

| Figure | X-axis | Y-axis | Lines |
|--------|--------|--------|-------|
| **Fig 1** Token reduction | Tool catalog size | Input tokens | Baseline, CYT |
| **Fig 2** Task success | Tool catalog size | Success rate | Baseline, CYT |
| **Fig 3** Cost per success | Tool catalog size | $/successful task | Baseline, CYT |

---

## Related work positioning

Do **not** claim CYT is the first system to reduce MCP tool-context overhead.

| Work | Approach | CYT differentiation |
|------|----------|---------------------|
| RAG-MCP | Tool-level retrieval | CYT transforms schemas (property + enum pruning), not just tool selection |
| Semantic Tool Discovery | Retrieval-based selection | Same |
| MCP-Zero | Agent-constructed toolchain | CYT is query-dependent schema minimization + verification |

**CYT contribution stack:**

```
Tool selection → Tool pruning → Optional-property pruning → Enum pruning → Schema reconstruction
                                                                          + Tool-call schema verification
```

---

## Differentiation from tool retrieval

Measure separately (see [metrics-instrumentation.md](./metrics-instrumentation.md)):

| Reduction type | Example |
|----------------|---------|
| `Reduction_tools` | 150 → 8 tools |
| `Reduction_properties` | 1,200 → 73 optional props |
| `Reduction_enums` | 3,800 → 214 enum values |

This supports the claim that CYT is a **schema minimizer**, not merely a top-k tool retriever.
