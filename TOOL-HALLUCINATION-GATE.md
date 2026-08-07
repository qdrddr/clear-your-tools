# Tool hallucination prevention (verify-only)

Block tool calls the agent invents or misuses — without injecting pruned tools or skills into the prompt.

CYT installs a **`preToolUse` hook** that validates each MCP tool call against a per-session **Type-2 tool
catalog**. Calls for tools not in the catalog, or with invalid arguments, are **denied** before execution.

This is **verify-only mode**: no prompt injection, no rules-file sync, no `postToolUse` capture.

---

## What gets enabled

`--prevent-hallucinations` writes this overlay to `~/.config/cyt/config.yaml`:

```yaml
hallucination_gate:
  enabled: true
skills:
  enabled: false
pruning:
  tools:
    enabled: false
  inject_via:
    cursor: hook
    claude: proxy   # wizard may prompt
    codex: proxy    # wizard may prompt
```

It also configures **cyt-mcp** in verify-only mode (`verify_only: true` in
`~/.config/cyt/mcp-aggregator.yaml`) and registers **`preToolUse`** hooks (plus lifecycle hooks for catalog
refresh).

---

## Install

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv tool install 'clear-your-tools[cyt-mcp]'
cyt hook cursor --prevent-hallucinations
```

Other agents:

```bash
cyt hook claude --prevent-hallucinations
cyt hook codex --prevent-hallucinations
cyt hook all --prevent-hallucinations
```

The wizard will:

1. Enable the hallucination gate and disable skills/tools injection.
2. Migrate backend MCP servers to `~/.config/cyt/mcp/<agent>.json`.
3. Register a single **cyt-mcp** entry in the agent MCP config.
4. Install CYT hooks (`preToolUse`, `beforeSubmitPrompt`, `sessionStart`, …).

**Restart the agent** when the wizard finishes. It may also restart the hook daemon for verify-only mode.

---

## How it works

```text
beforeSubmitPrompt / proxy request
        │
        ▼
  Build Type-2 catalog from cyt-mcp backend tools
  (stored in session JSONL under ~/.config/cyt/sessions/)
        │
        ▼
  Agent calls an MCP tool
        │
        ▼
  preToolUse → cyt-client → validate_pre_tool_call()
        │
        ├─ tool in catalog + args match schema → allow
        └─ unknown tool or schema mismatch       → deny
```

**Type-2 catalog** is the authority. Each catalog entry holds tool name + `input_schema` (no full tool bodies in
the gate path).

| Check | Result |
| --- | --- |
| Tool name in session Type-2 catalog | Allowed if args validate |
| Tool name missing from catalog | Denied |
| Args violate `input_schema` | Denied |
| No catalog yet (session not initialized) | Allowed (gate inactive) |

Denials are returned to the agent with a reason. CYT may append exposure metadata to the session log so later
calls can succeed after the catalog catches up.

---

## Agent-specific notes

### Cursor

- Catalog refresh: **`beforeSubmitPrompt`** → hook daemon `POST /hook/connect` (verify-only response).
- Hook daemon must be running (`sessionStart` runs `cyt hook daemon start`).
- No `.cursor/rules/cyt-injection.mdc` writes in verify-only mode.
- Add **cyt-mcp** to **Settings → Tools & MCP → allowlist** to avoid approval prompts ([CURSOR-HOOK.md](CURSOR-HOOK.md)).

### Claude Code / Codex

- Default tool detection path is **proxy** (`pruning.inject_via: proxy`).
- The proxy records the Type-2 catalog on each upstream request when verify-only is active.
- Run through CYT: `cyt launch -- claude` or `cyt launch -- codex`.

---

## Test locally

After install, the wizard prints a stdin test command. Example for Cursor:

```bash
echo '{"hook_event_name":"preToolUse","conversation_id":"test","tool_name":"mcp__cyt-mcp__filesystem_write_file","tool_input":{"path":"/tmp/x"},"workspace_roots":["/path/to/project"]}' \
  | CYT_LAUNCH_AGENT=cursor cyt-client
```

Enable debug logging: `CYT_HOOK_DEBUG=1`.

---

## Switch back to full CYT injection

Re-run hook setup without the flag:

```bash
cyt hook cursor
cyt setup   # re-enable skills/tools pruning if desired
```

Or edit `~/.config/cyt/config.yaml` manually — see [CONFIG.md](CONFIG.md).

Uninstall CYT hooks:

```bash
cyt hook all --uninstall
```
