# Cursor evaluation focus

Cursor-specific constraints, setup, and recommended eval path for the arXiv benchmark.

---

## Why Cursor first

| Factor | Implication |
|--------|-------------|
| Hook-only deployment | Represents the hardest integration path (no proxy token mutation) |
| cyt-mcp stub architecture | Production default for MCP tool gating |
| Rules-file injection | Unique to Cursor; must measure injected schema tokens explicitly |
| User audience | Primary CYT docs: `CURSOR-HOOK.md`, `examples/agents/cursor/` |

Claude/Codex proxy evals reuse `stats.db` with less custom instrumentation.

---

## Cursor deployment summary

```
Cursor IDE
  ├── MCP: cyt-mcp (stub tools, {} schemas on wire)
  ├── Hooks: cyt-client via ~/.cursor/hooks.json
  │     ├── sessionStart / sessionEnd
  │     ├── beforeSubmitPrompt → prune + write rules file
  │     ├── preToolUse → tool_gate (verify)
  │     └── postToolUse / preCompact
  └── Rules: .cursor/rules/cyt-injection.mdc (pruned full schemas)
```

**Verify-only:** Same hooks; no rules file sync; full schemas from cyt-mcp backends.

---

## Available experiment conditions

| Condition | Supported | Notes |
|-----------|-----------|-------|
| Baseline (full tools) | ✅ | Disable CYT; configure MCP backends directly in Cursor |
| CYT-prune (hook) | ✅ | `cyt hook cursor` |
| Verify-only | ✅ | `cyt hook cursor --prevent-hallucinations` |
| CYT-full | ✅ | Prune + enable hallucination gate in config |
| CYT proxy | ❌ | `LIMITATIONS.md` — E2E encryption |

---

## Cursor-specific measurement plan

### Token accounting (hook path)

CYT does not sit on the LLM wire for Cursor. Measure:

| Component | Measurement |
|-----------|-------------|
| **Stub overhead** | Tokenize cyt-mcp tool list (minimal schemas) |
| **Injected schema** | Tokenize `.cursor/rules/cyt-injection.mdc` after each `beforeSubmitPrompt` |
| **Effective tool tokens** | Stub + injected (approximates what model sees) |
| **Savings vs baseline** | baseline full MCP catalog tokens − effective CYT tokens |

Use `cyt-indexer-sdk` / `tiktoken` cl100k_base (same as `LIMITATIONS.md`).

### Baseline catalog capture

For fair comparison, snapshot full tool definitions:

```bash
cyt executor save   # or cloudflare save → ~/.config/cyt/mcp-definitions.json
```

Tokenize full catalog JSON as baseline \(T_{baseline}^{tools}\).

### Session artifacts

| File | Use in eval |
|------|-------------|
| `~/.config/cyt/sessions/<session>.jsonl` | Type-1/Type-2 catalogs, prune decisions |
| `~/.config/cyt/stats.db` | Limited for hook (check tools-hook endpoints) |
| Hook daemon logs | Prune errors, timing |

---

## Known platform limitations (include in paper)

| Limitation | Impact on eval |
|------------|----------------|
| No reverse proxy | Cannot compare proxy vs hook on Cursor — hook only |
| `additionalContext` not delivered on `beforeSubmitPrompt` | Rules file is measured path; document as Cursor workaround |
| Token usage not exposed in hook payload | May need provider dashboard for total input tokens |
| MCP initialized before session | cyt-mcp + session JSONL design addresses this — cite as motivation |

Code ready for native injection: `src/cyt_client/cursor.py` — re-evaluate when Cursor ships context delivery.

---

## Setup commands

```bash
uv tool install 'clear-your-tools[cyt-mcp]'
cyt hook cursor
# or
cyt hook cursor --prevent-hallucinations

# Backends
# ~/.config/cyt/mcp/cursor.json
# ~/.config/cyt/mcp-aggregator.yaml
```

Example config: `examples/agents/cursor/`

Restart Cursor after hook install.

---

## Recommended Cursor eval matrix (v1)

| Dimension | Value |
|-----------|-------|
| Agent | Cursor only |
| Configurations | 4 (ablation) |
| Catalog sizes | 25, 100, 250 |
| Tasks | 50 (5 smoke first) |
| Repetitions | 3 |

**Skip for v1:** Proxy comparison, Codex native pruning (separate runners).

---

## Distractor-heavy tasks on Cursor

Most important category for Cursor eval:

- cyt-mcp exposes **all** stub names to Cursor MCP layer
- Pruned **definitions** arrive via rules file
- Test: agent selects correct stub name → calls with valid args → backend executes

Failure modes to measure:

1. Required tool stub missing from prune → agent never calls it
2. Required enum pruned → agent uses invalid enum → verify-only catches (CYT-full)
3. Required optional property pruned → agent omits param → task fails assertion

---

## Integration with Cursor Cloud Agents

This research directory lives in-repo for the OSS package. Cursor Cloud Agent runs may use CYT hooks if environment installs `clear-your-tools` — document in reproducibility appendix.

Cloud agents **cannot** use local reverse proxy (`LIMITATIONS.md`).

---

## Checklist before first Cursor eval run

- [ ] `cyt hook cursor` installed and daemon running
- [ ] cyt-mcp backends configured and healthy
- [ ] Test repo/fixture credentials in `.env` (not committed)
- [ ] Baseline catalog snapshot saved and tokenized
- [ ] Task assertions run outside agent (deterministic)
- [ ] Session JSONL collection path configured in harness

See [eval-harness-spec.md](./eval-harness-spec.md) for implementation steps.
