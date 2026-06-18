# Claude Code

Point Claude Code at the proxy:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"
export OPENROUTER_API_KEY="..."
export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY}"
claude --model haiku 'say hi' -p
```

The default upstream in `config.yaml` is OpenRouter's Anthropic-compatible endpoint. Change
`network.proxy.reverse.upstreams` to target a different provider URL.

<details>
<summary><strong>Should I use .env</strong></summary>

We strongly recommend storing API keys via `cyt setup` (Keychain service **cyt**).
Shell exports and `~/.config/cyt/.env` also work.

```shell
cyt setup
export OPENROUTER_API_KEY="..."   # or rely on cyt keyring after setup
export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY}"
```

</details>

## Claude Code Desktop OR Claude Cowork

Both share the same config file (`~/.claude/settings.json`)

For the desktop app, point Claude Code Desktop or Claude Cowork at the proxy via `settings.json` instead of shell exports.

Edit `settings.json` in this directory (example `env.ANTHROPIC_BASE_URL`), then deep-merge it into your user config:

```bash
./examples/agents/anthropic/claude/claude-code-desktop-or-cowork.sh
```

The merge script is [`claude-code-desktop-or-cowork.sh`](./claude-code-desktop-or-cowork.sh):

- Merges `settings.json` into `~/.claude/settings.json` (creates the file and `~/.claude` if needed)
- Deep-merges nested objects so existing keys you did not override are kept
- Overlay values win on conflicts (e.g. `env.ANTHROPIC_BASE_URL` from this repo replaces yours)
- Requires [jq](https://jqlang.github.io/jq/) (`brew install jq` on macOS, `apt install jq` on Debian/Ubuntu)

Restart Claude Code Desktop or Claude Cowork after running the script so it picks up the merged settings.

## Skills hook (`cyt hook`)

Enable skill injection in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
  inject_via: hook   # or proxy (proxy appends skills to system message in request body)
```

With `inject_via: proxy`, the proxy appends matched skills to the system message on each request;
hooks are optional and `UserPromptSubmit` produces no injection when hooks remain configured.

Copy or merge [`hooks.json`](./hooks.json) into `~/.claude/settings.json`. For local development,
point the hook at the absolute path to [`hooks/cyt-skills.sh`](./hooks/cyt-skills.sh) (uses the repo
`.venv/bin/python`). Set `CYT_SKILLS_DEBUG=1` in the environment or pass `--debug` via a wrapper to
log stdin to `.debug/skills/` (project `cwd`) and `~/.config/cyt/debug/skills/`.

Example `~/.claude/settings.json` hook entry:

```json
"command": "/path/to/clear-your-tools/examples/agents/anthropic/claude/hooks/cyt-skills.sh"
```

Simulate hooks locally:

```bash
CYT_REPO=/path/to/clear-your-tools
TRANSCRIPT=/path/to/session.jsonl   # Claude Code session jsonl with message.model on assistant rows

echo '{"hook_event_name":"UserPromptSubmit","session_id":"test","prompt":"configure agent hooks","transcript_path":"'"$TRANSCRIPT"'","cwd":"'"$CYT_REPO"'"}' \
  | uv run --directory "$CYT_REPO" src/cyt/proxy/cli.py hook --stdin --debug
```

- **UserPromptSubmit** (Claude Code) — stdin includes `session_id`, `prompt`, and `transcript_path`
  but not `model`. `cyt hook --stdin` reads `message.model` from the transcript jsonl (latest assistant
  row). BM25-matched skill chunks are returned as `additionalContext`. Debug logs record the
  resolved model as `details.resolved_model`.
- **SessionStart** — not required; legacy `SessionStart` hooks are ignored if still configured.

The first inject may build the per-skill catalog under `~/.config/cyt/skills/entries/`; later runs
reuse cached decomposed indexes.
