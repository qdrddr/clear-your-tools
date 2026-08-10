# Cursor evaluation focus

Cursor is the **v1 harness target** (hook-only). See [evaluation-framework.md](./evaluation-framework.md) for four-level metrics.

---

## Deployment (Cursor only)

| Condition | Supported |
|-----------|-----------|
| No CYT | ✅ Native MCP |
| Verify only | ✅ `--prevent-hallucinations` |
| Pruning only | ✅ `cyt hook cursor` |
| Pruning + Verify | ✅ Config overlay |
| Proxy | ❌ Platform limit |

---

## Architecture on Cursor

```
Cursor IDE
  ├── MCP: cyt-mcp stubs ({})
  ├── Hooks: cyt-client
  │     ├── beforeSubmitPrompt → prune → rules file
  │     ├── preToolUse → tool_gate (L1)
  │     └── preCompact → compaction reset
  └── Rules: .cursor/rules/cyt-injection.mdc
```

Stable stubs + dynamic inject — relevant for prefix-stability discussion even on hook path.

---

## Measuring three primary results on Cursor

### 1. Tokens

| Component | Measure |
|-----------|---------|
| Tool stubs | Tokenize cyt-mcp tool list |
| Injected defs | Tokenize `cyt-injection.mdc` per turn |
| Tool-context total | stubs + injected |
| Total input/output | Estimate from trace or spot-check |

Baseline: tokenize full catalog from backend snapshot.

### 2. Cost

- BM25 pruner: $0
- Agent cost: model pricing × tokens (flat input rate for v1)
- CostPerSuccess: total / L4 success

### 3. Task quality (L4)

Deterministic verifiers only — **ignore** tool-call success for `RunResult.success`.

---

## Tool-call logging on Cursor (L1)

| Signal | Source |
|--------|--------|
| Allow/deny | `preToolUse` → parse deny reason |
| Schema-valid | Infer from allow vs schema deny |
| Blocked (C) | deny with schema error |
| Execution (L2) | MCP result (logged, not headline) |
| Retry (L3) | Same tool name, later step after deny (logged) |

Parse `~/.config/cyt/sessions/*.jsonl` for Type-2 catalog and deny exposures.

---

## v1 matrix

| Dimension | Value |
|-----------|-------|
| Primary | No CYT vs cyt-prune |
| Verification | No CYT vs verify-only + static corpus |
| Tasks | 5 smoke → ~50 Layer B |
| Catalog sizes | 25, 100, 250 |
| Pipeline | BM25 |
| Aggregator | CYT-MCP |
| Repetitions | 3 |

Claude/Codex proxy eval deferred — richer `stats.db` token data when extending.

---

## Known limitations (paper §9)

| Limitation | Eval impact |
|------------|-------------|
| No proxy | Cannot compare hook vs proxy on Cursor |
| Rules file vs additionalContext | Document workaround |
| Token usage opaque | Schema token estimate + manual validation |
| Flat pricing | No cached/uncached split in v1 |

---

## Setup

```bash
uv tool install 'clear-your-tools[cyt-mcp]'
cyt hook cursor
# ~/.config/cyt/mcp/cursor.json
# ~/.config/cyt/mcp-aggregator.yaml
```

Restart Cursor after hook install. Example: `examples/agents/cursor/`.

---

## Checklist

- [ ] 4 configurations via harness overlay
- [ ] Per-call JSONL log (ToolCallRecord)
- [ ] L4 verifier independent of tool metrics
- [ ] Baseline catalog tokenized
- [ ] Session JSONL + rules file collected per run
- [ ] MPR computed for verify runs + static corpus
