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

We strongly recommend using password vaults like macOS KeyChain

```shell
# Store key in secure vault
security add-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w "sk-..."  # macOS

# Now you can access the key like this:
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
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

## Skills hook (`cyt skills`)

Enable skill injection in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
```

Merge [`hooks.json`](./hooks.json) into `~/.claude/settings.json` (or reference
[`hooks/cyt-skills.sh`](./hooks/cyt-skills.sh) if `cyt` is not on the hook `PATH`). The hook runs on
`SessionStart` (registers the session model) and `UserPromptSubmit` (BM25-matches skill chunks into
`additionalContext`).

The first inject may build the per-skill catalog under `~/.config/cyt/skills/entries/`; later runs
reuse cached decomposed indexes.
