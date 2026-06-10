# Codex

Point Codex at the proxy (same pattern as Claude Code) and enable skills injection.

## Skills hook (`cyt skills`)

Enable in `~/.config/cyt/config.yaml`:

```yaml
skills:
  enabled: true
```

Copy or merge [`hooks.json`](./hooks.json) into `~/.codex/hooks.json`. If `cyt` is not on the hook
`PATH`, use the wrapper [`hooks/cyt-skills.sh`](./hooks/cyt-skills.sh) as the hook command instead.

- **SessionStart** — registers `session_id` and `model` in `~/.config/cyt/cache.db`
- **UserPromptSubmit** — BM25-matches skill chunks and returns `additionalContext` when skills survive scoring

The first inject may build cached skill indexes under `~/.config/cyt/skills/entries/`; later runs reuse them.
