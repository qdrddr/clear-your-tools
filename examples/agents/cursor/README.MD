# Cursor

Cursor hooks use a different config layout than Claude Code or Codex:

- Config: `~/.cursor/hooks.json` (or project `.cursor/hooks.json`)
- Events: `sessionStart`, `beforeSubmitPrompt`, … (camelCase — not Claude's `SessionStart` / `UserPromptSubmit`)

See [Cursor Hooks docs](https://cursor.com/docs/hooks) for the full event list.

## Install example hooks

Merge [`hooks.json`](./hooks.json) into `~/.cursor/hooks.json`:

```bash
./examples/agents/cursor/cursor-hooks-install.sh
```

Restart Cursor after installing so hooks reload.

## Skills hook (`cyt hook cursor`)

Enable skill injection in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
  inject_via: hook   # or proxy (proxy appends skills to the request body)
```

With `inject_via: proxy`, the proxy appends matched skills on each request; hooks are optional.

Install Cursor hooks into `~/.cursor/hooks.json`:

```bash
cyt hook cursor
```

[`hooks.json`](./hooks.json) wires:

- **`sessionStart`** → `cyt-client` (delete stale rules file), then `cyt hook daemon start` (colocated hook HTTP server)
- **`beforeSubmitPrompt`** → `CYT_LAUNCH_AGENT=cursor cyt-client` (prune + inject via rules file)
- **`sessionEnd`** → `cyt-client` (delete rules file)

## Rules-file injection (Cursor IDE)

Cursor IDE does **not** deliver hook `additional_context` on `beforeSubmitPrompt` today. CYT works around this by
writing pruned injection to `.cursor/rules/cyt-injection.mdc` with `alwaysApply: true` — the most reliable way to
inject context into every prompt in Cursor IDE. It lacks per-prompt selectivity at the Cursor layer, but pruned
`<agent-skills>` and `<agent-tools>` reach the model end-to-end because CYT rewrites the file on each prompt (and
deletes it when nothing survives pruning).

| Event | cyt-client behavior |
| --- | --- |
| `sessionStart` | Delete rules file (clear leftovers from prior session) — no HTTP |
| `beforeSubmitPrompt` | POST `/hook/inject`, write rules file when content changed |
| `sessionEnd` | Delete rules file — no HTTP |

The rules file is auto-appended to `.gitignore` in git workspaces. Set `CYT_CURSOR_RULES_FILE=0` to disable rules-file sync.

`cyt-client` adapts Cursor stdin/stdout automatically:

| Cursor | cyt |
| --- | --- |
| `beforeSubmitPrompt` | `UserPromptSubmit` |
| `workspace_roots[0]` | `cwd` |
| `conversation_id` | `session_id` |
| `hookSpecificOutput.additionalContext` | `additional_context` + `continue: true` (ignored by Cursor IDE; use rules file) |

Set `CYT_SKILLS_DEBUG=1` to log hook stdin under `~/.config/cyt/debug/skills/`.

Ensure `cyt` / `cyt-client` and the hook daemon are on `PATH`.

Simulate a hook locally:

```bash
echo '{"hook_event_name":"beforeSubmitPrompt","conversation_id":"test","prompt":"configure agent hooks","workspace_roots":["/path/to/project"],"model":"claude-sonnet-4-20250514"}' \
  | CYT_LAUNCH_AGENT=cursor cyt-client
```

- **`beforeSubmitPrompt`** (Cursor) — fires when the user sends a prompt. Cursor may include `model` on the payload;
otherwise BM25 matching uses the prompt text alone.
- **`sessionStart`** — deletes any stale rules file, then starts the hook daemon.
- **`sessionEnd`** — deletes the rules file when the chat session closes.

The first inject may build cached skill indexes under `~/.config/cyt/skills/entries/`; later runs reuse them.

Skill directories for Cursor: `.cursor/skills/` in the project and `~/.cursor/skills/` in the home directory.
