# Debug mode

How to run the reverse proxy with pruning debug output and inspect which tools were kept vs dropped.

## cyt-indexer CLI

Check how the app mutates the requests by decomposing tools and then re-composed based on survivors
to see what the tool will change using [cyt-indexer-cli.sh](../scripts/cyt-indexer-cli.sh) script.

## Install and run

From the repo (or any machine with the project deps):

```bash
uv tool install 'clear-your-tools[all]'

uv run cyt proxy --debug
```

`--debug` logs each transformed request to `.debug/anthropic.log` (see `network.proxy.reverse.debug_log_dir` in config)
and still forwards to upstream. It also enables full tool JSON in stats (`store_full_tools` is turned on while debug
is active).

Dry-run (no upstream call):

```bash
uv run cyt proxy --debug-dry-run
```

Point Claude Code at the proxy:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"
export OPENROUTER_API_KEY="..."
export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY}"
```

Pruning needs API keys for the configured pipeline (e.g. `OPENROUTER_API_KEY`, `DEEPINFRA_API_KEY`).
See `scripts/proxy.sh` for a local example.

## SQLite: full tool lists before and after pruning

Default DB path: `~/.config/cyt/stats.db` (`stats.database.path` in config).

Each proxied request is recorded in `proxy_request`. Related tables: `model_request`
(per-stage upstream/pruning calls) and `tokens` (token counts per model request).

### `proxy_request` columns (current schema)

| Column | Type | Meaning |
| -------- | ------ | --------- |
| `id` | TEXT | UUID7 primary key (not an integer; do not use `MAX(id)` for “latest”) |
| `endpoint` | TEXT | Route name (e.g. `anthropic`) |
| `tools_in` | INTEGER | Tool **token** count before pruning |
| `tool_count_in` | INTEGER | Number of tools before pruning |
| `tool_properties_count_in` | INTEGER | Optional property count before pruning |
| `tools_out` | INTEGER | Tool **token** count after pruning |
| `tool_count_out` | INTEGER | Number of tools after pruning |
| `tool_properties_count_out` | INTEGER | Optional property count after pruning |
| `tools_pruned` | INTEGER | Tool tokens saved |
| `tool_count_pruned` | INTEGER | Tools removed |
| `tool_properties_count_pruned` | INTEGER | Optional properties removed |
| `ts_ms` | INTEGER | Request time (Unix ms); use for ordering |
| `prune_status` | TEXT | e.g. `applied`, `skipped`, `error` |
| `pipeline` | TEXT | JSON array of pruning stages |
| `query` | TEXT | Text used for retrieval/rerank |
| `error` | TEXT | Pruning error message, if any |
| `tools_accepted_json` | TEXT | Full tools JSON before pruning (`store_full_tools` or `--debug`) |
| `tools_final_json` | TEXT | Full tools JSON after pruning (`store_full_tools` or `--debug`) |

Only some rows store full tool JSON (proxy run with `--debug` or `stats.store_full_tools: true`). Use this filter on
every investigation query:

```sql
WHERE tools_accepted_json IS NOT NULL
  AND length(tools_accepted_json) > 0
```

Example queries (all scoped to rows with stored tool JSON):

```bash
# Last few investigable rows (pick an id for the queries below)
sqlite3 ~/.config/cyt/stats.db "
SELECT id, datetime(ts_ms / 1000, 'unixepoch') AS ts
FROM proxy_request
WHERE tools_accepted_json IS NOT NULL
  AND length(tools_accepted_json) > 0
ORDER BY ts_ms DESC
LIMIT 10;
"

# One row by id (paste id from the list query above) and save into a .json file
ID=019e6c0a-035c-7bd6-ab8b-75492296b553
sqlite3 ~/.config/cyt/stats.db "
SELECT tools_accepted_json
FROM proxy_request
WHERE id = '${ID}'
  AND tools_accepted_json IS NOT NULL
  AND length(tools_accepted_json) > 0;
" > tools_accepted.json

sqlite3 ~/.config/cyt/stats.db "
SELECT tools_final_json
FROM proxy_request
WHERE id = '${ID}'
  AND tools_final_json IS NOT NULL
  AND length(tools_final_json) > 0;
" > tools_final.json
```

CLI summaries (token totals, not per-tool JSON):

```bash
cyt stats totals
cyt stats events --limit 20
```

To persist full tool JSON without `--debug`, set `stats.store_full_tools: true` in `~/.config/cyt/config.yaml`.

## `.debug/anthropic.log`: snapshot per request

Each block is a JSON snapshot (timestamp header, then payload). Useful fields:

- **`body.tools`** — tools after pruning (what would be sent upstream); these are the tools that “survived.”
- **`pruning`** — metadata for the same request:
  - `input.tools` — full copy of request `tools` before pruning (unchanged from the client payload)
  - `tools_in` / `tools_out` — tool counts before and after pruning
  - `status` — e.g. `applied`, `skipped`, `error`
  - `query` — text used for retrieval/rerank
  - token and decomposed breakdown fields when pruning ran

For side-by-side JSON outside the log file, `tools_accepted_json` / `tools_final_json` in SQLite still apply when
`store_full_tools` is enabled (on by default with `--debug`).

Console lines while `--debug` is on also print a one-line summary, e.g. `pruning status: applied (tools 30 -> 28)`.

## Related

- README: [Debug without calling upstream](README.md) (`--debug-dry-run`)
- Default listen port: **8834**
- Log rotation: large `anthropic.log` files may appear as `anthropic.log.1` under `.debug/`

## Request Example

To investigate the request prior and after pruning:

3 main parts to look at:

- User Query saved in `content.messages:` (role: user)
- Final tools after pruning is in `tools:`
- Original request tools are in `pruning.input.tools`
- Decomposed optional properties per stage are in `pruning.decomposed_catalog.<stage>.json`; enums are in `md`

See as short example in [example.json](example.json)
