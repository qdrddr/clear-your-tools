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
| `rerank` | Qwen3-Reranker-8B (DeepInfra)          | ≥ `pruning.policy.minimum_tools` (default **50**)                     | Scores every catalog chunk against the user query; drops low-scoring tools and optional props.   |
| `llm`    | Mercury 2 or GPT-OSS-120B (OpenRouter) | ≥ `pruning.policy.minimum_tools` (default **50**), after `rerank`     | LLM selects which catalog chunks to keep; can remove entire tools more aggressively.             |

**Recommendations:**

- **Fewer than ~30 tools** — pruning is skipped automatically; the overhead is usually not worth it.
- **30–50 tools** — enable the **`rerank`** pipeline (default). This is the sweet spot for the
  reranker pruner.
- **50+ tools** — keep **`rerank`** or use **`llm`**. rerank can be pipelined into LLM as a second
  stage (`pipeline: [rerank, llm]`) for stronger tool-level filtering on large catalogs.

---

## Configuration

User settings live in **`~/.config/cyt/config.yaml`** (created on first `cyt proxy` run). Values are layered on
top of the packaged [`defaults.yaml`](src/cyt/config/defaults.yaml). You can also use `./config.yaml` in the
working directory or pass `--config`.

<details>
<summary><strong>Update Thresholds</strong></summary>

Configure thresholds in [`~/.config/cyt/config.yaml`](~/.config/cyt/config.yaml) (deep-merged with bundled
[`defaults.yaml`](src/cyt/config/defaults.yaml); see [Configuration](#configuration) below).

```yaml
pruning:
  tools:
    policy:
      minimum_tools: 50
    sequence:
      - rerank
      # - llm
```

Legacy `pruning.pipeline`, `pruning.policy.*`, and `models.rerankers.minimum_tools` /
`models.llm.minimum_tools` still work; see [Schema migration](#schema-migration) below.

</details>

<details>
<summary><strong>Update model pricing (stats)</strong></summary>

`cyt stats` uses `input_cost_per_token` and `output_cost_per_token` under each model entry in
`models.llm.remote` (upstream models) and `models.rerankers.remote` (reranker). Update these when provider
prices change so net-savings numbers stay accurate.

Example — Claude Sonnet 4.6 on Anthropic / OpenRouter (`nick: sonnet` in defaults):

```yaml
# ~/.config/cyt/config.yaml
models:
  llm:
    remote:
      - nick: sonnet
        name: claude-sonnet-4-6
        provider: anthropic
        key_var_name: ANTHROPIC_API_KEY
        pricing:
          input_cost_per_token: 3e-06   # $3 / 1M input tokens
          output_cost_per_token: 15e-06 # $15 / 1M output tokens
```

**Important:** `models.llm.remote` is a YAML **list**. Copy every entry you still need from
[`defaults.yaml`](src/cyt/config/defaults.yaml) (upstream model, pruning LLM, reranker), then adjust `pricing`.
If your file does not define `models.llm.remote` yet, bundled pricing is used as-is.

Reranker pricing (DeepInfra Qwen3-Reranker-8B):

```yaml
models:
  rerankers:
    remote:
      - nick: rerank-qwen3-8b
        pricing:
          input_cost_per_token: 5e-08 # $0.05 / 1M input tokens
```

</details>

### Switch from Rerank to LLM pruner

Default pipeline is **`rerank` only**. To use the LLM pruner instead (or after rerank):

```yaml
# ~/.config/cyt/config.yaml
pruning:
  tools:
    sequence:
      - llm          # LLM only (no reranker)
      # - rerank     # or: [rerank, llm] for two-stage filtering
    policy:
      minimum_tools: 50   # remote stages run when tool count ≥ this (default 50)
    pipelines:
      llm:
        model_nick: mercury-2   # catalog nick under models.llm.remote
```

Legacy `pruning.pipeline`, `pruning.llm.model.remote.model_nick`, and
`defaults.remote.llm_model_nick` still work.

Rerank & LLM prunners can use any providers that supported by underlying [LiteLLM Client SDK](https://docs.litellm.ai/docs/providers).

| Pipeline | API keys needed |
| -------- | ----------------- |
| `[rerank]` | Key for `pruning.rerank.model.remote.model_nick` (see below). Default `DEEPINFRA_API_KEY` |
| `[llm]` | Key for `pruning.llm.model.remote.model_nick` (see below) |
| `[rerank, llm]` | Both |

With **`llm` only**, you can skip `DEEPINFRA_API_KEY`. The LLM stage is stronger at dropping whole tools;
rerank is cheaper and better for the 30–50 tool range.

<details>
<summary><strong>Choose LLM pruning model (OpenRouter vs OpenAI)</strong></summary>

Set **`pruning.llm.model.remote.model_nick`** to a `nick` under `models.llm.remote`. Bundled options:

| `model_nick` | Provider | Model | Env var |
| ---------------- | -------- | ----- | ------- |
| `mercury-2` | OpenRouter | `inception/mercury-2` | `OPENROUTER_API_KEY` |
| `gpt-oss-120b` | OpenRouter | `openai/gpt-oss-120b` | `OPENROUTER_API_KEY` |
| `gemini-3-flash` | OpenRouter | `google/gemini-3-flash-preview` | `OPENROUTER_API_KEY` |
| `gpt-5.4-nano` | OpenAI | `gpt-5.4-nano` | `OPENAI_API_KEY` |

Example — OpenRouter (default-style):

```yaml
pruning:
  pipeline:
    - llm
  llm:
    model:
      remote:
        model_nick: gpt-oss-120b
```

Example — OpenAI direct:

```yaml
pruning:
  pipeline:
    - llm
  llm:
    model:
      remote:
        model_nick: gpt-5.4-nano
```

```bash
export OPENAI_API_KEY="..."
```

To add another model, append an entry under `models.llm.remote` with `nick`, `name`, `provider`,
`key_var_name`, and `pricing`, then point `pruning.llm.model.remote.model_nick` at that `nick`.

Full defaults: [`src/cyt/config/defaults.yaml`](src/cyt/config/defaults.yaml). See [`DEV.md`](DEV.md) for the
rest of the config surface.

</details>

<details>
<summary><strong>Pruning policies</strong></summary>

Two tool categories with different defaults:

| Category         | Default policy   | Examples                  | Typical prefix      |
| ---------------- | ---------------- | ------------------------- | ------------------- |
| **System tools** | `prune_optional` | `Read`, `Write`, `Agent`  | (no `mcp__` prefix) |
| **MCP tools**    | `prune_all`      | Tools from MCP servers    | `mcp__…`            |

Set defaults in `config.yaml`:

```yaml
pruning:
  policy:
    system_tool: prune_optional
    mcp_tool: prune_all
```

Legacy `defaults.system_tool_policy` / `defaults.mcp_tool_policy` are still supported.

</details>

<details>
<summary><strong>Policy options</strong></summary>

| Policy                        | Behavior                                                                     |
| ----------------------------- | ---------------------------------------------------------------------------- |
| `always_include`              | Full tool schema every turn; no pruning.                                     |
| `prune_optional`              | Tool always included; irrelevant optional properties dropped.                |
| `prune_all`                   | Entire tool may be removed if irrelevant.                                    |
| `prune_optional_descriptions` | Scoring like `prune_optional`; pruned optionals return without descriptions. |
| `prune_all_descriptions`      | Scoring like `prune_all`; tool always kept with required props — see below.  |

**Description policies:** `prune_optional_descriptions` always includes the tool and required
properties (with descriptions). Non-surviving optional properties are reinstated without
descriptions (names, types, and enum hints remain). `prune_all_descriptions` behaves the same
when the tool root survives scoring; when the root is pruned, only required properties are
reinstated (descriptions stripped) and optional properties are omitted.

Per-pipeline overrides (optional): set `pruning.bm25.policy`, `pruning.rerank.policy`, or
`pruning.llm.policy` with the same `system_tool` / `mcp_tool` / `per_tool` shape. When a stage is
the terminal pipeline step, its policy overrides the main `pruning.policy` for output/reinstatement.

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

</details>

---

## Quick start

Requires uv tool.
Install [uv](https://docs.astral.sh/uv/getting-started/installation)

### 1. Install proxy

From PyPI (proxy + pruners):

```bash
uv tool install 'clear-your-tools[all]'
```

Copy API keys (or use `~/.config/cyt/.env`):

```bash
cp .env.example .env
# Edit .env — at minimum DEEPINFRA_API_KEY (reranker) and OPENROUTER_API_KEY or OPENAI_API_KEY (upstream + optional LLM stage)
```

<details>
<summary><strong>Though we strongly recommend storing keys via `cyt setup` (Keychain service "cyt")</strong></summary>

```shell
cyt setup   # interactive; stores keys in Keychain service "cyt"

# Optional manual Keychain access (advanced; cyt uses account "__credentials__" JSON blob)
security find-generic-password -s "cyt" -a "__credentials__" -w
```

</details>

### 2. Configure the proxy

Interactive wizard (writes `~/.config/cyt/config.yaml` and optionally `~/.config/cyt/.env`):

```bash
cyt setup
```

Or edit `~/.config/cyt/config.yaml` manually — see [Configuration](#configuration).
Bundled defaults include rerank via DeepInfra Qwen3-Reranker-8B (`DEEPINFRA_API_KEY`).

### 3. Run the proxy

Installed CLI:

```bash
uv run cyt proxy
```

Default listen port: **8834** (from bundled `defaults.yaml` or `~/.config/cyt/config.yaml`).

### 4. Run the the Agent

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
uv run cyt stats totals
uv run cyt stats summary --period day
uv run cyt stats events --limit 20
```

Stats are stored in `~/.config/cyt/stats.db` by default.

---

## FAQ

<details>
<summary><strong>Doesn't pruning burn more tokens than it saves?</strong></summary>

The reranker and weak LLM used for pruning are **much cheaper per token** than the main model
(e.g. Claude Sonnet). You may spend extra tokens on pruning, but they cost a fraction of what you
save on the main request. Set `input_cost_per_token` and `output_cost_per_token` in
[`~/.config/cyt/config.yaml`](#configuration) to track savings.

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
- **Which pruning policy you use** — see [Pruning policies](#configuration).

To estimate savings on a captured request JSON, see [`DEV.md`](DEV.md).
To see statistics of actual net savings (input tokens) run:

```bash
uv run cyt stats totals
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
<summary><strong>Claude Code reports ZlibError when using the proxy</strong></summary>

The proxy must not decompress upstream responses while still forwarding `Content-Encoding: gzip`.
If it does, Claude Code’s `fetch` inflates already-plain JSON/SSE and raises **`ZlibError`**.
This is not fixed by installing zlib for Node or Python.

Upgrade `cyt` to a version that pass-through streams compressed bytes to clients. Confirm with:

```bash
curl --raw -sS -D - -o /tmp/cyt-msg.body ... # POST via http://127.0.0.1:8834/anthropic/...
head -c 4 /tmp/cyt-msg.body | xxd   # 1f8b when Content-Encoding is gzip
```

Set `ANTHROPIC_BASE_URL` to **`http://localhost:<port>/anthropic`** (see [Run the proxy](#3-run-the-proxy)),
not `https://`, unless you enable `network.proxy.reverse.http2.serve` with TLS certificates.

</details>

<details>
<summary><strong>Uvicorn logs Invalid HTTP request received</strong></summary>

Default `cyt proxy` uses **plain HTTP** (uvicorn).
Clients that open **`https://`** to that port send a TLS handshake; uvicorn logs this warning and rejects the connection.

Use `http://` in `ANTHROPIC_BASE_URL`, or configure Hypercorn TLS via
`network.proxy.reverse.http2.serve` and `http2.ssl` in this file.

</details>

---

## Development

See [`DEV.md`](DEV.md) for checkout setup, repository layout, library usage, and configuration reference.

---

## Tool hook injection

When `pruning.tools.inject_via` is `hook`, tool definitions are pruned inside `cyt hook --stdin`
and injected into the agent turn via `hookSpecificOutput.additionalContext` (same channel as
skills hook injection). The reverse proxy does not mutate request `tools` arrays in this mode.

**Cursor IDE:** `beforeSubmitPrompt` does not deliver `additional_context` to the model. When using
`cyt-client` hooks, CYT writes pruned injection to `.cursor/rules/cyt-injection.mdc` with
`alwaysApply: true` instead — the most reliable delivery path in Cursor IDE today. The file is
rewritten per prompt (deleted before each inject, then rewritten when content changes), deleted on
empty injection, and cleaned up on `sessionEnd`. Set `CYT_CURSOR_RULES_FILE=0` to disable. See
[`examples/agents/cursor/README.MD`](examples/agents/cursor/README.MD).

| Key | Values | Default |
| --- | ------ | ------- |
| `pruning.tools.inject_via` | `proxy` \| `hook` | `proxy` |
| `pruning.tools.hook.tools_from` | `executor` \| `definitions` | `executor` |
| `pruning.tools.hook.executor_url` | URL | `http://localhost:4789` |
| `pruning.tools.hook.executor_token_var` | env var name | `EXECUTOR_TOKEN` |
| `pruning.tools.hook.mcp_definitions_file` | path | `~/.config/cyt/mcp-definitions.json` |

- **`proxy`**: CYT prunes tools from the upstream request `tools` array via the reverse proxy (BM25,
  rerank, and LLM pipelines run on that request catalog only). Executor is **not** used in proxy
  mode — `pruning.tools.hook.executor_*` settings are ignored, and the proxy works without Executor
  running or `EXECUTOR_TOKEN` set.
- **`hook`**: load a catalog from the definitions file or live executor HTTP API (`tools_from: executor`),
  run the BM25/rerank/LLM pipeline, inject `<agent-tools>…</agent-tools>` context. Missing catalog
  files or an unset executor URL are skipped silently at hook time (configure with `cyt hook`, `cyt setup`, or optional
  `cyt launch` repair prompt).

### Proxy injection placement (`network.proxy.reverse.inject_into_user_message`)

When `pruning.inject_via` is `proxy`, you can control **where** pruned MCP tools and matched skills
land in the upstream HTTP body. Claude Code uses the `/anthropic` endpoint; Codex uses `/openai`
(OpenAI Responses API). Both agents share this flag.

| Key | Values | Default |
| --- | ------ | ------- |
| `network.proxy.reverse.inject_into_user_message` | `true` \| `false` | `true` |

- **`false`**: pruned tools replace the root `tools` array; skills inject into the system message
  (Claude Code) or a developer message before the last user turn (Codex).
- **`true` (default)**: system tools stay in root `tools`; pruned **MCP** tools are removed from `tools` and
  injected as `<agent-tools>…</agent-tools>` into the **latest user message**. **Minimal MCP stubs** (name,
  empty schema, and for Codex the original `description` on each stub) remain in `tools[]` so the agent can invoke
  native MCP `tool_use` calls; full pruned defs (name + `input_schema` only, no description on `<tool>` tags)
  live in the user-turn inject block. Skills inject as
  `<agent-skills>…</agent-skills>` into that same user turn (not system/developer). Tool pruning
  still uses the normal BM25/rerank/LLM query extraction — only the injection anchor moves to the
  last user turn to preserve provider prompt-cache prefixes. Ignored when `inject_via: hook`.

```yaml
network:
  proxy:
    reverse:
      inject_into_user_message: true
```

Live executor catalog loading requires **hook injection** (`pruning.inject_via: hook`), a running
Executor MCP aggregator at `pruning.tools.hook.executor_url`, and a Bearer token in `EXECUTOR_TOKEN`
(or `pruning.tools.hook.executor_token_var`). Snapshot tools offline with `cyt executor save`.
Proxy injection does not load Executor catalogs or MCP cache.

`cyt launch` skips the reverse proxy when **both** tools and skills use hook injection (or skills are
disabled). Mixed mode (`skills.inject_via: proxy`, `tools.inject_via: hook`) still starts the proxy
for skills injection.

---

## Schema migration

Canonical config shape (since vNext):

| Old path | New path |
| -------- | -------- |
| `pruning.pipeline` | `pruning.tools.sequence` |
| `pruning.policy.*`, `pruning.per_tool` | `pruning.tools.policy.*` |
| `pruning.bm25` / `rerank` / `llm` | `pruning.tools.pipelines.<id>` |
| `pruning.<id>.model.remote.model_nick` | `pruning.tools.pipelines.<id>.model_nick` |
| Inline model `provider`, `key_var_name`, … | `models.providers[]` + model `provider_nick` |

Old keys continue to work; resolution lives in `src/cyt/config/legacy.py` for easy removal later.
`save_user_config` skips disk writes when the merged config is unchanged (no rearrange-on-save).

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
