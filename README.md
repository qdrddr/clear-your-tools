# Clear Your Tools

**Clear Your Tools** is a reverse proxy for coding agents such as
[Claude Code](https://docs.anthropic.com/en/docs/claude-code). It sits between the agent and upstream
LLM providers (Anthropic-compatible APIs on OpenRouter, Novita, DeepInfra, and others), intercepts
each request, and shrinks the tool payload before forwarding it upstream. Can be easily adopted for
other harness agents.

Large MCP catalogs can add tens of thousands of tokens of tool-schema overhead on every turn.
Clear Your Tools removes irrelevant tools and trims irrelevant optional parameters while always
keeping required fields for tools that stay in the request.

---

## How it works

```text
Agent (Claude Code, etc.)
        │
        ▼
Clear Your Tools proxy  ──► extract user query from messages
        │                   decompose each tool schema
        │                   score / filter with reranker (or LLM pruning)
        │                   recompose pruned tool list
        ▼
Upstream provider (OpenRouter, Anthropic, Novita, …)
```

On each intercepted request the proxy:

1. **Extracts the user query** from the conversation (latest user turn, with message cleanup).
2. **Decomposes tool schemas** into a catalog of chunks: each tool root keeps required properties;
   optional properties are split into separate searchable units.
3. **Runs the pruning pipeline** configured in `config.yaml` (default: `rerank`; or `llm`).
4. **Recomposes surviving tools** — required properties always remain; only optional properties
   that look relevant to the query are merged back in.
5. **Forwards the modified request** to the upstream provider with the smaller `tools` array.

### Pruning pipeline

| Stage    | Model (default)                        | When it runs                                                          | What it does                                                                                     |
| -------- | -------------------------------------- | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `rerank` | Qwen3-Reranker-8B (DeepInfra)          | ≥ `models.rerankers.minimum_tools` tools (default **29**)             | Scores every catalog chunk against the user query; drops low-scoring tools and optional props.   |
| `llm`    | Mercury 2 or GPT-OSS-120B (OpenRouter) | ≥ `models.llm.minimum_tools` tools (default **50**), after `rerank`   | LLM selects which catalog chunks to keep; can remove entire tools more aggressively.             |

**Recommendations:**

- **Fewer than ~30 tools** — pruning is skipped automatically; the overhead is usually not worth it.
- **30–50 tools** — enable the **`rerank`** pipeline (default). This is the sweet spot for the
  reranker pruner.
- **50+ tools** — keep **`rerank`** or use **`llm`**. rerank can be pipelined into LLM as a second
  stage (`pipeline: [rerank, llm]`) for stronger tool-level filtering on large catalogs.

Configure thresholds in `config.yaml` (or `~/.config/cyt/config.yaml`):

```yaml
models:
  rerankers:
    minimum_tools: 29
  llm:
    minimum_tools: 50

pruning:
  pipeline:
    - rerank
    # - llm
```

Full example of [config file is here](src/cyt/config/defaults.yaml).

---

## Quick start

Requires Python 3.13+ (see [`pyproject.toml`](pyproject.toml)).

### Install

From PyPI (proxy + pruners):

```bash
uv pip install 'clear-your-tools[all]'
# or
uv tool install 'clear-your-tools[all]'
```

For local development, dependencies are managed with [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync --all-extras
```

Copy API keys (or use `~/.config/cyt/.env`):

```bash
cp .env.example .env
# Edit .env — at minimum DEEPINFRA_API_KEY (reranker) and OPENROUTER_API_KEY (upstream + optional LLM stage)
```

Though we strongly recommend using password vaults like macOS KeyChain

```shell
# Store key in secure vault
security add-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w "sk-..."  # macOS

# Now you can access the key like this:
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
```

### Run the proxy

Installed CLI:

```bash
cyt-rproxy serve --port 8834
```

From a dev checkout:

```bash
uv run cyt-rproxy serve --port 8834
```

Default listen port: **8834** (from bundled `defaults.yaml` or `~/.config/cyt/config.yaml`).

Point Claude Code at the proxy:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"
export OPENROUTER_API_KEY="..."
export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY}"
claude --model haiku 'say hi' -p
```

The default upstream in `config.yaml` is OpenRouter's Anthropic-compatible endpoint. Change
`network.proxy.reverse.upstreams` to target a different provider URL.

### Debug without calling upstream

```bash
cyt-rproxy serve --debug-dry-run --port 8834
```

Writes transformed request snapshots to `{endpoint}.log` (e.g. `anthropic.log`).

### View pruning stats savings

```bash
cyt-rproxy stats totals
cyt-rproxy stats summary --period day
cyt-rproxy stats events --limit 20
```

Stats are stored in `~/.config/cyt/stats.db` by default.

---

## HTTP/2 and TLS

Some clients prefer HTTP/2. Generate a local certificate (gitignored under `src/crt/`):

```bash
mkdir -p src/crt
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout src/crt/key.pem \
  -out src/crt/cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Trust the cert on macOS: Keychain Access → System → import `cert.pem` → Trust → "Always Trust".

Run with HTTP/2:

```bash
uv pip install h2 'hypercorn[h2]'
cyt-rproxy serve --http2-serve \
  --ssl-keyfile src/crt/key.pem \
  --ssl-certfile src/crt/cert.pem \
  --port 8834
```

TLS settings can also live in `config.yaml` under `network.proxy.reverse.http2.ssl`.

---

## Pruning policies

Two tool categories with different defaults:

| Category         | Default policy   | Examples                  | Typical prefix      |
| ---------------- | ---------------- | ------------------------- | ------------------- |
| **System tools** | `prune_optional` | `Read`, `Write`, `Agent`  | (no `mcp__` prefix) |
| **MCP tools**    | `prune_all`      | Tools from MCP servers    | `mcp__…`            |

Set defaults in `config.yaml`:

```yaml
defaults:
  system_tool_policy: prune_optional
  mcp_tool_policy: prune_all
```

### Policy options

| Policy           | Behavior                                                                                            |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| `always_include` | No pruning — full tool schema every turn.                                                           |
| `prune_optional` | Tool always included; irrelevant **optional** properties dropped. Required properties always kept.  |
| `prune_all`      | Entire tool may be removed if irrelevant. If kept, required properties stay; optional ones trimmed. |

`prune_all` on MCP tools saves the most tokens. With ~100 tools, expect up to **~95% reduction in
tool-schema tokens**.

### Per-tool overrides

```yaml
pruning:
  per_tool:
    Agent: prune_optional
    mcp__hedl__hedl_convert_from: prune_optional
    mcp__hedl__batch: prune_all
    mcp__fff__multi_grep: always_include
```

---

## FAQ

<details>
<summary><strong>Doesn't pruning burn more tokens than it saves?</strong></summary>

The reranker and weak LLM used for pruning are **much cheaper per token** than the main model
(e.g. Claude Sonnet). You may spend extra tokens on pruning, but they cost a fraction of what you
save on the main request. Add `input_cost_per_token` and `output_cost_per_token` to `config.yaml`
to track savings.

**Example pricing (input tokens):**

| Model               | Cost per 1M input tokens |
| ------------------- | ------------------------ |
| Claude Sonnet 4.6   | $3.00                    |
| Qwen-Reranker-8B    | $0.050                   |
| GPT-OSS-120B        | $0.14                    |
| Inception Mercury 2 | $0.25                    |

The weak models such as Mercury 2 or GPT-OSS-120B returns only the IDs of tools to keep, so its
output stays extremely small. Rerankers do not count output tokens and are usually much cheaper
than a strong LLM.

**Rule of thumb:** saving 1M Sonnet input tokens is still worthwhile even if pruning uses up to
~10M Mercury tokens — roughly a 1:10 cost ratio. The reranker has roughly a 1:60 cost ratio.

In practice, pruning usually adds modest overhead. Worst case (no tools pruned), you might pay
~$3.30 instead of $3.00. With typical pruning (40–95% of tool tokens removed), tool-schema cost
drops from ~$3.00 to roughly **$0.15–$1.80**, plus ~$0.30 for pruning — about **$0.45–$2.10 total**
for tool-related cost, or roughly **30–85% savings** depending on policy.

</details>

<details>
<summary><strong>Why don't I see 30–85% savings on my total request?</strong></summary>

Those numbers apply to **tool schemas only** of the **input tokens only**, not the full prompt (system message, conversation
history, user message, etc.). Clear Your Tools prunes tools based on the user request; the rest of
the request is unchanged.

How much you save overall depends on:

- **How many tools you have** — more MCP servers mean a larger share of the request is tool
  schemas. We do not recommend using CYT below 50 tools.
- **Which pruning policy you use** — see [Pruning policies](#pruning-policies).

Estimate total savings on a captured request:

```bash
uv run count_request_tokens.py \
  --tool-savings-percent 85 \
  --requestfile temp_example_claude_call.json
```

`temp_example_claude_call` can be obtained from the proxy running in debug mode.

With ~100 tools and `prune_all`, expect **~85–95% savings on tool tokens** and typically **~30%+
savings on the full request**. The more tools you have the more overall savings you'll see.

</details>

<details>
<summary><strong>Where can I see how many tools and parameters an MCP server has?</strong></summary>

The popular [Fetch](https://mcpmarket.com/server/fetch) MCP server is a good example. On its
**Tools** tab: 4 tools, each with 4 parameters (1 required, 3 optional) — 16 parameters total.

If the user asks to "fetch the Markdown of a webpage", the `prune_all` typically keeps only the
**Fetch Markdown** tool with its required parameter plus any optional parameters that look
relevant. Unrelated tools (e.g. **Read file**) are dropped entirely.

</details>

---

## Repository layout

```text
.
├── README.md
├── pyproject.toml
├── count_request_tokens.py      # estimate savings on a captured request JSON
└── src/
    └── cyt/                       # installable package (Clear Your Tools)
        ├── config/                # load_config, defaults.yaml
        ├── common/                # catalog_paths, token_usage, pricing
        ├── indexer/               # build, retrieve, catalog_io
        ├── pruners/               # llm, rerank, policies
        └── proxy/                 # transport, reverse, anthropic, stats, cli
```

### Library usage

```python
from cyt.indexer import CatalogIndex, build_catalog_index, load_catalog, retrieve_tools
from cyt.pruners import rerank_catalog_dict, llm_catalog_dict
from cyt.pruners.policies import configure_policies_from_config
from cyt.proxy.reverse import create_app  # requires clear-your-tools[proxy]
```

---

## Configuration reference

Main config file: `config.yaml` in the working directory, or
[`~/.config/cyt/config.yaml`](~/.config/cyt/config.yaml) (created on first run).
Bundled defaults ship in the package as `cyt.config.defaults.yaml`.

| Section                                                   | Purpose                                                  |
| --------------------------------------------------------- | -------------------------------------------------------- |
| `defaults.system_tool_policy` / `mcp_tool_policy`         | Default pruning behavior for system vs MCP tools         |
| `defaults.remote.reranking_model_nick` / `llm_model_nick` | Model nicknames for pruning stages                       |
| `pruning.pipeline`                                        | Ordered list of stages: `rerank`, `llm`                  |
| `pruning.per_tool`                                        | Per-tool policy overrides                                |
| `models.rerankers` / `models.llm`                         | Remote model definitions, API keys, minimum tool counts  |
| `network.proxy.reverse`                                   | Listen port, upstream URLs, HTTP/2, TLS                  |
| `stats`                                                   | Stats DB path, optional full tool JSON storage           |

Environment variables (see [`src/.env.example`](src/.env.example)):

- `DEEPINFRA_API_KEY` — reranker stage
- `OPENROUTER_API_KEY` — upstream forwarding and optional LLM stage

---

## Limitations

This implementation requires running as reverse proxy with supported agents such as Claude Code,
Codex, OpenCode etc.

Cursor for instance can't run with reverse proxy and only supports forward proxy, though the
requests sent via forward proxy are still encrypted and not visible for manipulation and pruning.

This functionality logically is more suitable to be accompanied with an MCP Aggregator that takes
all the tools from actual MCP servers on backend and serves only the relevant tools to the agent.
Though in theory sound concept, in practice MCP protocol Specification has limitations not allowing
this to happen:

- MCP is not designed to be integrated with Agent hooks
- MCP Client and servers are initialized before agent starts its session leading to MCP is not
  aware of agent sessions or sub-agents and can't reliably target which agent session or subagent
  see which tools, so pruning would become unreliable.

---

## License

See [`LICENSE`](LICENSE).
