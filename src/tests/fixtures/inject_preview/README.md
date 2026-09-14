# inject preview fixtures

Regression fixtures for `cyt inject preview` workspace-scoped catalog loading.

## Background

The cyt-mcp hook catalog disk cache is keyed by a **workspace-scoped slug** (global MCP
fingerprint + workspace MCP fingerprint). CLI callers must set `_cyt_hook_workspace_root`
before loading the master catalog; otherwise they resolve the global-only slug and see an
empty catalog even when a workspace disk cache exists.

## Files

- `catalog_tools.json` — minimal BM25-related cyt-mcp tools (3 entries)
- `scenarios.json` — integration expectations for preview output

## Related tests

- `src/tests/unit/test_inject_cli.py` — workspace scoping unit coverage
- `src/tests/integration/test_inject_preview_integration.py` — end-to-end preview CLI
