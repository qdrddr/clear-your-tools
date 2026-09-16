# cyt_mcp session log wire name regression fixtures

Prevents the regression where session logs stored bare backend `tool_name`
(`grep`) instead of the cyt-mcp frontend wire `name` (`fff_grep`), breaking
pre-exposure verbatim matching against injection XML.

## Files

- `catalog_tools.json` — enriched hook catalog tools (`name`, `server_key`, `tool_name`)
- `scenarios.json` — parametrized expectations for unit and integration tests
- `legacy_session_entries.json` — old wrong-format session log rows (bare names)

## Regression invariant

For `cyt_mcp` tools downstream of catalog build:

- Session log `key` = `tool:cyt_mcp:{wire_name}`
- Session log `name` = wire `name` from catalog (never bare `tool_name`)
- Injection XML `name='…'` must match session log `name` for verbatim skip

Legacy bare-only entries are intentionally **not** repaired; tests assert they
do not skip wire-format injection.
