# Codex

Point Codex at the proxy (same pattern as Claude Code) and enable skills injection.

## Skills hook (`cyt hook`)

Enable in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
  inject_via: hook   # or proxy (proxy inserts developer message in request body)
```

With `inject_via: proxy`, the proxy inserts matched skills into each Codex request; hooks are optional
and `UserPromptSubmit` produces no injection when hooks remain configured.

Copy or merge [`hooks.json`](./hooks.json) into `~/.codex/hooks.json`. **Do not use bare `uv run` in
Codex hooks** — Codex runs hooks with a minimal `PATH` (`shell_environment_policy inherit = "core"`),
so `uv` is usually not found and the hook fails before any logging.

For local development, point the hook at the absolute path to
[`hooks/cyt-skills.sh`](./hooks/cyt-skills.sh) (uses the repo `.venv/bin/python`). Set
`CYT_SKILLS_DEBUG=1` in the environment or pass `--debug` via a wrapper to log stdin to
`.debug/hooks/` (Codex project `cwd`) and `~/.config/cyt/debug/hooks/`.

Example `~/.codex/hooks.json` entry:

```json
"command": "/path/to/clear-your-tools/examples/agents/openai/codex/hooks/cyt-skills.sh"
```

Ensure `skills.enabled: true` in `~/.config/cyt/config.yaml`.

Simulate a hook locally:

```bash
CYT_REPO=/path/to/clear-your-tools
echo '{"hook_event_name":"UserPromptSubmit","session_id":"test","model":"gpt-4","prompt":"configure agent hooks","cwd":"'"$CYT_REPO"'"}' \
  | uv run --directory "$CYT_REPO" src/cyt/proxy/cli.py hook --stdin --debug
```

- **UserPromptSubmit** (Codex) — Codex sends `session_id`, `model`, and `prompt` on every turn; a
  `UserPromptSubmit` hook alone is enough for injection and stats. `cyt hook --stdin` reads `model` from
  the hook payload.

The first inject may build cached skill indexes under `~/.config/cyt/skills/entries/`; later runs reuse them.
