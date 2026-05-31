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

**Tool Recommendations:**

- **50+ tools** — keep **`rerank`** or use **`llm`**. rerank can be pipelined into LLM as a second
  stage (`pipeline: [rerank, llm]`) for stronger tool-level filtering on large catalogs.

**Pipeline & Model Recommendations**: Choose your pipeline based on model cost:

- **Expensive models** (≥$3/M input tokens, e.g. Sonnet): Use an **LLM pruner** pipeline.
- **Cheap models** ($0.10–$1/M input tokens, e.g. Haiku, Gemini 3 Flash): Use a **rerank** pipeline with a low-cost model.
- **Premium models** (e.g. Opus): Use an **LLM pruner + rerank** combined pipeline.

---

## Quick start

Requires uv tool.
Install [uv](https://docs.astral.sh/uv/getting-started/installation)

### 1. Install proxy

From PyPI (proxy + pruners):

```bash
uv tool install 'clear-your-tools[all]'
```

<details>
<summary><strong>Though we strongly recommend using password vaults like macOS KeyChain</strong></summary>

```shell
# Store key in secure vault
security add-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w "sk-..."  # macOS

# Now you can access the key like this:
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
```

</details>

<details>
<summary><strong>Configure the proxy (optional)</strong></summary>

Interactive wizard (writes `~/.config/cyt/config.yaml` and optionally `~/.config/cyt/.env`):

```bash
uv run cyt-rproxy setup
```

Or edit `~/.config/cyt/config.yaml` manually — see [CONFIG.md](CONFIG.md).
</details>

### 2. Run the proxy --upstream <https://api.anthropic.com> --upstream-kind anthropic

Installed CLI:

```bash
uv run cyt-rproxy serve
```

Default listen port: **8834** (from bundled `defaults.yaml` or `~/.config/cyt/config.yaml`).

When optional step of proxy setup with the prunner pipeline & costs was skipped, we fallback to BM25,
though tracking costs has started.
Run `cyt-rproxy setup` to setup tracking costs and advance prunning pipelines.

### 3. Run the the Agent

Point Claude Code at the proxy:

```bash
export ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"
export OPENROUTER_API_KEY="..."
export ANTHROPIC_AUTH_TOKEN="${OPENROUTER_API_KEY}"
claude --model haiku 'say hi' -p
```

The default upstream in `config.yaml` is OpenRouter's Anthropic-compatible endpoint. Change
`network.proxy.reverse.upstreams` to target a different provider URL.

### 5. View pruning stats savings

```bash
uv run cyt-rproxy stats totals
uv run cyt-rproxy stats summary --period day
uv run cyt-rproxy stats events --limit 20
```

Stats are stored in `~/.config/cyt/stats.db` by default.

---

## FAQ

<details>
<summary><strong>Doesn't pruning burn more tokens than it saves?</strong></summary>

The reranker and weak LLM used for pruning are **much cheaper per token** than the main model
(e.g. Claude Sonnet). You may spend extra tokens on pruning, but they cost a fraction of what you
save on the main request. Set `input_cost_per_token` and `output_cost_per_token` in
[`~/.config/cyt/config.yaml`](CONFIG.md#configuration) to track savings.

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
- **Which pruning policy you use** — see [Pruning policies](CONFIG.md#configuration).

To estimate savings on a captured request JSON, see [`DEV.md`](DEV.md).
To see statistics of actual net savings (input tokens) run:

```bash
uv run cyt-rproxy stats totals
```

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

<details>
<summary><strong>Is my provider/model supported?</strong></summary>

CYT's **pruner models** (the cheap reranker and LLM that decide which tools to keep) call providers through [LiteLLM](https://docs.litellm.ai/docs/providers).
If LiteLLM supports your provider and model, you can use them in CYT.

When you run `cyt-rproxy setup` and add a pruner model, you'll be prompted for:

- **Provider** — LiteLLM provider route, without a trailing slash (e.g. `openai`, `openrouter`).
- **Model name** — LiteLLM model string (see the [provider docs](https://docs.litellm.ai/docs/providers)).
- **API key env var** — the *name* of the environment variable that holds your key,
not the key itself (e.g. `OPENAI_API_KEY`, `OPENROUTER_API_KEY`).
- **domain_match** — hostname from the provider's API base URL (e.g. `openai.com` for OpenAI, `openrouter.ai` for OpenRouter).
Used to match outgoing requests to the right model config.

</details>

<details>
<summary><strong>Claude Code reports ZlibError when using the proxy</strong></summary>

Install missing zlib:

```bash
npm install -g zlib
brew install zlib
```

This usually means the proxy returned a **`Content-Encoding: gzip`** (or `deflate`) header with a body
that was **already decompressed**. Claude Code’s `fetch` then tries to inflate plain JSON/SSE and fails.
It is **not** a missing zlib install on your machine or in CYT.

**Fix:** upgrade to a `cyt-rproxy` build that streams upstream bytes unchanged (`aiter_raw` pass-through).
After upgrading, verify:

```bash
curl --raw -sS -D - -o /tmp/cyt-msg.body \
  -H 'Accept-Encoding: gzip' \
  ... # your POST to http://127.0.0.1:8834/anthropic/v1/messages
head -c 4 /tmp/cyt-msg.body | xxd   # should show 1f8b when header says gzip
```

**Also check:** `ANTHROPIC_BASE_URL` must use **`http://`** for the default plain-HTTP server,
e.g. `http://localhost:8834/anthropic`. Using **`https://`** against `cyt-rproxy serve` (without TLS/`http2.serve`)
causes uvicorn’s `Invalid HTTP request received` and broken API calls.

</details>

<details>
<summary><strong>Uvicorn logs Invalid HTTP request received</strong></summary>

`cyt-rproxy serve` listens for **HTTP/1.1** on the configured port (default **8834**).
This warning almost always means a client connected with the wrong protocol:

- **`https://localhost:8834`** while the proxy is plain HTTP → TLS handshake bytes, not HTTP
- HTTP/2 prior knowledge to uvicorn (use `http2.serve` + TLS certs only if you intend HTTPS)

Use `http://localhost:8834/anthropic` unless you have enabled Hypercorn TLS in config.

</details>

---

## Development

See [`DEV.md`](DEV.md) for checkout setup, repository layout, library usage, and configuration reference.

---

## Limitations

See [`LIMITATIONS.md`](LIMITATIONS.md) for deployment constraints, token accounting caveats, and MCP aggregator trade-offs.

## Debug

See details to debug pruning in [debug/](debug/).

---

## License

<details>
<summary><strong>Inspiration</strong></summary>

This project is inspired by the ideas explored in the [tool-attention](https://github.com/asadani/tool-attention) project,
particularly around improving tool selection efficiency and reducing unnecessary tool exposure to the model.

It also aims to limit the effects of [context rot](https://www.trychroma.com/research/context-rot)
by pruning irrelevant or confusing tools from the available toolset based on the current user prompt and execution context.

Reducing irrelevant tools helps decrease prompt noise, lowers cognitive load on the model,
and can improve tool selection accuracy and overall agent reliability.

</details>

See [`LICENSE`](LICENSE).
