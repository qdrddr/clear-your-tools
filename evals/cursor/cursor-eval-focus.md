# Cursor evaluation focus

Cursor is the **primary v1 harness target** (hook-only). See [evaluation-framework.md](./evaluation-framework.md) for four-level metrics.

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

Stable stubs + dynamic inject — relevant for **cache/prefix** discussion even on hook path (MCP layer stable).

---

## Measuring three primary results on Cursor

### 1. Tokens

| Component | Measure |
|-----------|---------|
| Tool stubs | Tokenize cyt-mcp tool list |
| Injected defs | Tokenize `cyt-injection.mdc` per turn |
| Tool-context total | stubs + injected |
| Total input | **Gap** — Cursor may not expose; estimate or spot-check |
| Latest agent message | Extract from trace for turn-aware ablation |

Baseline: tokenize full catalog from `cyt executor save` / backend snapshot.

### 2. Cost

- BM25 pruner: $0
- Agent cost: model pricing × tokens (cached/uncached if available)
- CostPerSuccess: total / L4 success

### 3. Task quality (L4)

Deterministic verifiers only — **ignore** tool-call success for `RunResult.success`.

---

## Tool-call logging on Cursor (L1–L3)

| Signal | Source |
|--------|--------|
| Allow/deny | `preToolUse` → parse deny reason |
| Schema-valid | Infer from allow vs schema deny |
| Blocked (C) | deny with schema error |
| Execution (L2) | MCP `postToolUse` result or backend response |
| Retry (L3) | Same tool name, later step after deny |
| allowed_unexposed | Allowed + tool not in rules file inject but in Type-2 |

Parse `~/.config/cyt/sessions/*.jsonl` for Type-2 catalog and deny exposures.

---

## Cursor-specific ablations

| Ablation | How |
|----------|-----|
| Pre-exposure | Compare with filter disabled (fork config) |
| Compaction | Multi-turn task triggering `preCompact` |
| Turn-aware prune | Log `combined_text` vs user-only query |

**Code:** `pre_exposed.py`, `test_session_compaction.py`.

---

## v1 matrix (Cursor)

| Dimension | Value |
|-----------|-------|
| Primary | No CYT vs cyt-prune |
| Verification subset | No CYT vs verify-only |
| Tasks | 5 → 50 (Layer B) |
| Catalog sizes | 25, 100, 250 |
| Pipeline | BM25 |
| Aggregator | CYT-MCP |
| Repetitions | 3 |

Add Claude/Codex proxy for paper — richer `stats.db` token data.

---

## Known limitations (paper §9)

| Limitation | Eval impact |
|------------|-------------|
| No proxy | Cannot compare hook vs proxy on Cursor |
| Rules file vs additionalContext | Document workaround |
| Token usage opaque | Schema token estimate + manual validation |
| Type-2 admission | Model may call unexposed catalog tools — log rate |

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
- [ ] MPR/TESR computed for verify runs
