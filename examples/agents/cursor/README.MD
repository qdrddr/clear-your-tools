# Cursor

Cursor hooks use a different config layout than Claude Code or Codex:

- Config: `~/.cursor/hooks.json` (or project `.cursor/hooks.json`)
- Scripts: `~/.cursor/hooks/` (when installed globally)
- Events: `sessionStart`, `beforeSubmitPrompt`, … (camelCase — not Claude's `SessionStart` / `UserPromptSubmit`)

See [Cursor Hooks docs](https://cursor.com/docs/hooks) for the full event list.

## Install example hooks

Merge [`hooks.json`](./hooks.json) into `~/.cursor/hooks.json` with an absolute path to this repo's hook script:

```bash
./examples/agents/cursor/cursor-hooks-install.sh
```

To wire hooks manually, point `beforeSubmitPrompt` at the repo script:

```json
"command": "CYT_LAUNCH_AGENT=cursor /path/to/clear-your-tools/examples/agents/cursor/hooks/cyt-skills.sh"
```

Restart Cursor after installing so hooks reload.

## Skills hook (`cyt hook`)

Enable skill injection in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
  inject_via: hook   # or proxy (proxy appends skills to the request body)
```

With `inject_via: proxy`, the proxy appends matched skills on each request; hooks are optional.

[`hooks.json`](./hooks.json) wires:

- **`sessionStart`** → `cyt hook daemon start` (colocated hook HTTP server)
- **`beforeSubmitPrompt`** → [`hooks/cyt-skills.sh`](./hooks/cyt-skills.sh) via [`hooks/cursor-hook-bridge.py`](./hooks/cursor-hook-bridge.py)

The bridge maps Cursor event names and I/O to what `cyt-client` expects:

| Cursor | cyt |
| --- | --- |
| `beforeSubmitPrompt` | `UserPromptSubmit` |
| `workspace_roots[0]` | `cwd` |
| `conversation_id` | `session_id` |
| `hookSpecificOutput.additionalContext` | `additional_context` + `continue: true` |

Set `CYT_SKILLS_DEBUG=1` to log hook stdin under `~/.config/cyt/debug/skills/`.

Ensure `cyt` / `cyt-client` and the hook daemon are on `PATH`, or use the repo `.venv` via the wrapper script
(same pattern as Claude/Codex examples).

Simulate a hook locally:

```bash
CYT_REPO=/path/to/clear-your-tools
echo '{"hook_event_name":"beforeSubmitPrompt","conversation_id":"test","prompt":"configure agent hooks","workspace_roots":["'"$CYT_REPO"'"],"model":"claude-sonnet-4-20250514"}' \
  | CYT_LAUNCH_AGENT=cursor uv run --directory "$CYT_REPO" python examples/agents/cursor/hooks/cursor-hook-bridge.py
```

- **`beforeSubmitPrompt`** (Cursor) — fires when the user sends a prompt. Cursor may include `model` on the payload;
otherwise BM25 matching uses the prompt text alone.
- **`sessionStart`** — starts the hook daemon; does not inject skills by itself.

The first inject may build cached skill indexes under `~/.config/cyt/skills/entries/`; later runs reuse them.

Skill directories for Cursor: `.cursor/skills/` in the project and `~/.cursor/skills/` in the home directory.
