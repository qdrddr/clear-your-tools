# Changelog

## 1.0.0

Major release: agent modules reorganization. Old import paths were removed without deprecation shims.

### Migration

| Old import | New import |
| --- | --- |
| `cyt.launch.claude` | `cyt.agents.claude.launch` |
| `cyt.launch.codex` | `cyt.agents.codex.launch` |
| `cyt.launch.cursor` | `cyt.agents.cursor.launch` |
| `cyt.skills.hook_setup` | `cyt.hook.setup_wizard` |
| Cursor payload normalize in `cyt_client` | Server: `cyt.agents.cursor.skills_hook.normalize_cursor_payload` |
| Anthropic proxy skills helpers in `cyt.skills.proxy_inject` | `cyt.agents.claude.skills_proxy` (facade re-exports remain) |
| OpenAI proxy skills helpers in `cyt.skills.proxy_inject` | `cyt.agents.codex.skills_proxy` (facade re-exports remain) |
| `cyt.common.agents` | Still valid; types also in `cyt.agents._types` |

### Highlights

- `cyt/agents/{claude,codex,cursor}/` — launch, hook install, proxy wiring, skills_hook, skills_proxy
- `cyt_client` stays stdlib-only; sends `cyt_agent` and raw Cursor JSON; server normalizes
- Cursor agent-transcript JSONL parsing for skills search query
- Optional extras: `claude`, `codex`, `cursor`, `agents`
