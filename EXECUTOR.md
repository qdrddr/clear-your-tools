# Executor

[Executor](https://github.com/UsefulSoftwareCo/executor) is a local MCP aggregator and
Code Mode sandbox. Clear Your Tools integrates with Executor **only in hook injection mode**
(`pruning.inject_via: hook`) when `pruning.tools.hook.tools_from` is `executor` (the default for
hook injection) in `~/.config/cyt/config.yaml`. Proxy injection does not use Executor.

---

## Install

```bash
npm install -g executor   # or: pnpm add -g / bun add -g / yarn global add
executor install          # install the durable background service
executor web              # open the web UI in your browser
```

The integrations page is usually at:

<http://localhost:4789/integrations>

---

## Configure

Log in to Executor, then:

1. **Copy your Authentication Bearer Key** from the UI. Use the raw token only — do not include a
   `Bearer` prefix.
2. **Add at least one MCP server** under Integrations. Examples: [Context7](https://context7.com) or
   [DeepWiki](https://deepwiki.com).

Export the token for CYT:

Run `cyt hook daemon status`; it should prompt for `EXECUTOR_TOKEN` and store it in the keyring.

```bash
export EXECUTOR_TOKEN='<your-token>'
```

Or add it to `~/.config/cyt/.env`. CYT reads the env var named by
`pruning.tools.hook.executor_token_var` (default: `EXECUTOR_TOKEN`).

---

## Use with CYT

With Executor running and at least one MCP server configured:

- **Hook injection** — `cyt hook` fetches tools from `http://localhost:4789` (override with
  `pruning.tools.hook.executor_url` in `~/.config/cyt/config.yaml`). Requires
  `pruning.inject_via: hook`.
- **Proxy injection** — does not use Executor; tools are pruned from the upstream request body.
- **Offline snapshot** — `cyt executor save` writes the current catalog to
  `~/.config/cyt/mcp-definitions.json`.
- Cached requests are also saved to `~/.config/cyt/cache/executor-catalog/` dir

See [CONFIG.md](CONFIG.md#tool-hook-injection) for hook and executor settings.
