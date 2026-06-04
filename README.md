<!-- markdownlint-disable MD041 -->

# Clear Your Tools

<table border="0">
  <tr>
    <td valign="top" width="260">
      <img src="assets/logo.png" alt="Clear Your Tools" width="240">
    </td>
    <td valign="top">

Think token reduction is only about lowering costs by 30%?

- ⚡ Faster local & cloud LLMs: fewer tokens, less Context Delusion.
- 🎯 Better results: reduce Context Rot, keep the model focused on the task.

Your AI agent sees only the tools relevant to the current user task and intent.

✅ BM25 ranking by default
✅ No API keys required
✅ Works transparently

</td>
  </tr>
</table>

<!-- markdownlint-disable MD041 -->
<div align="center">

[![Quick Start][quick-start-shield]](#quick-start)
[![License][license-badge-shield]][license-link]
![No Telemetry][telemetry-shield]

[![version][version-shield]][release-link]
[![discord][discord-shield]][discord-link]

![Shell][shell-shield]
![Python][python-tech-shield]
![TypeScript][typescript-shield]
![Rust][rust-tech-shield]

  <table>
    <tr>
      <td align="center" width="120">
        <img src="assets/claude.png" width="60" alt="Claude Code"/><br/>
        <b>Claude Code</b>
      </td>
      <td align="center" width="120">
        <img src="assets/codex.png" width="60" alt="Codex"/><br/>
        <b>Codex</b>
      </td>
    </tr>
  </table>

</div>
<!-- markdownlint-enable MD041 -->

**Clear Your Tools** is a reverse proxy for coding agents such as
[Claude Code](https://github.com/anthropics/claude-code) and [Codex CLI](https://github.com/asadani/tool-attention/tree/main/examples/agents).

**The problem:**

- Excess context leads to [Context Rot](https://www.trychroma.com/research/context-rot),
and removing irrelevant information consistently improves an LLM’s cognitive performance.
- [Context Dilution](https://diffray.ai/blog/context-dilution/): 11 out of 12 models dropped below 50%
of their baseline performance at just 32K tokens.
- Locally running LLMs to work faster needs less context at the input.

Our Proxy sits between the agent and upstream
LLM providers (Anthropic-compatible APIs on OpenRouter, Novita, DeepInfra, and others), intercepts
each request, and shrinks the tool payload before forwarding it upstream. Can be easily adopted for
other harness agents.

Examples of how to run these agents with the proxy can be found in the [`./examples/agents`](./examples/agents) directory.

Large MCP catalogs can add tens of thousands of tokens of tool-schema overhead on every turn.
Clear Your Tools removes irrelevant tools and trims irrelevant optional parameters while always
keeping required fields for tools that stay in the request.

---

## How it works

<p align="center">
  <img src="assets/cyt-savings1.gif" alt="Example cyt stats output showing token savings" />
</p>

```text
Agent (Claude Code, etc.)
        │
        ▼
Clear Your Tools proxy  ──► extract user query from messages
        │                   decompose each tool schema
        │                   score / filter with BM25 (default), rerank, or LLM pruning
        │                   recompose pruned tool list
        ▼
Upstream provider (OpenRouter, Anthropic, Novita, …)
```

On each intercepted request the proxy:

1. **Extracts the user query** from the conversation (latest user turn, with message cleanup).
2. **Decomposes tool schemas** into a catalog of chunks: each tool root keeps required properties;
   optional properties are split into separate searchable units.
3. **Runs the pruning pipeline** configured in `config.yaml`. Out of the box the default is
   **`bm25`** (local, no API keys). After `cyt setup`, choose between **`rerank`**
   (optionally followed by **`llm`**).
4. **Recomposes surviving tools** — required properties always remain; only optional properties
   that look relevant to the query are merged back in.
5. **Forwards the modified request** to the upstream provider with the smaller `tools` array.

<details>
<summary><strong>Pruning pipelines</strong></summary>

| Stage    | Model (default)                        | When it runs                                                                                                                     | What it does                                                                                                                       |
| -------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `bm25`   | Local BM25 index (`bm25s`)             | Default pipeline when no remote pruner is configured; also fallback when rerank/llm fail or tool count is below their thresholds | Scores catalog chunks locally against the user query; no API keys or pruning cost. Indexes are cached under `~/.config/cyt/bm25/`. |
| `rerank` | Qwen3-Reranker-8B (DeepInfra)          | ≥ `models.rerankers.minimum_tools` tools (default **50**), after `cyt setup`                                                     | Scores every catalog chunk against the user query; drops low-scoring tools and optional props.                                     |
| `llm`    | Mercury 2 or GPT-OSS-120B (OpenRouter) | ≥ `models.llm.minimum_tools` tools (default **50**), after `rerank`                                                              | LLM selects which catalog chunks to keep; can remove entire tools more aggressively.                                               |

**Tool Recommendations:**

- **Getting started / no setup** — the default **`bm25`** pipeline works out of the box with no
  remote API keys.
- **50+ tools** — run **`cyt setup`** and use **`rerank`** or **`llm`**. Rerank can be pipelined
  into LLM as a second stage (`pipeline: [rerank, llm]`) for stronger tool-level filtering on
  large catalogs.

**Pipeline & Model Recommendations**: Choose your pipeline based on model cost:

- **Expensive models** (≥$3/M input tokens, e.g. Sonnet): Use an **LLM pruner** pipeline.
- **Cheap models** ($0.10–$1/M input tokens, e.g. Haiku, Gemini 3 Flash): Use a **rerank** pipeline with a low-cost model.
- **Premium models** (e.g. Opus): Use an **LLM pruner + rerank** combined pipeline.

</details>

---

## Supported platforms

<div align="center">

[![Windows][windows-shield]](#supported-platforms)
[![macOS][macos-shield]](#supported-platforms)
[![Linux][linux-shield]](#supported-platforms)

</div>

Clear Your Tools and the `cyt-indexer` SDKs support **Windows**, **macOS**, and **Linux**.

<details>
<summary><strong>SDK & CLI</strong></summary>

All language bindings wrap the same Rust core: decompose tool schemas into searchable catalog
chunks, then recompose tools from a survivor list. See [cyt-indexer-cli.sh](./search/cyt-indexer-cli.sh)
<!-- markdownlint-disable MD013 -->
<table border="0">
  <tr>
    <td valign="top">

**`cyt-indexer-sdk`** ([PyPI][pypi-sdk-link])
    </td>
    <td valign="top">

Python SDK (`cyt_indexer`)
    </td>
    <td valign="top">

[![PyPI cyt-indexer-sdk][pypi-sdk-version-shield]][pypi-sdk-link]

[![PyPI downloads][pypi-sdk-downloads-shield]][pypi-sdk-link]
    </td>
  </tr>
  <tr>
    <td valign="top">

**`cyt-indexer-sdk`** ([npm][npm-link])
    </td>
    <td valign="top">

TypeScript SDK
    </td>
    <td valign="top">

[![npm cyt-indexer-sdk][npm-sdk-version-shield]][npm-link]

[![npm downloads][npm-sdk-downloads-shield]][npm-link]
    </td>
  </tr>
  <tr>
    <td valign="top">

**`clear-your-tools`** ([PyPI][pypi-cyt-link])
    </td>
    <td valign="top">

Python SDK (`import cyt`) and CLI (`cyt`: `proxy` / `pruners`)
    </td>
    <td valign="top">

[![PyPI clear-your-tools][pypi-cyt-version-shield]][pypi-cyt-link]

[![PyPI downloads][pypi-cyt-downloads-shield]][pypi-cyt-link]
    </td>
  </tr>
  <tr>
    <td valign="top">

**`cyt-indexer`** ([crates.io][rust-link])
    </td>
    <td valign="top">

Rust library and CLI (`build` / `retrieve`)
    </td>
    <td valign="top">

[![crates.io cyt-indexer][rust-version-shield]][rust-link]

[![crates.io downloads][rust-downloads-shield]][rust-link]
    </td>
  </tr>
</table>
<!-- markdownlint-disable MD013 -->
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

### 2. Run the proxy

Installed CLI:

```bash
uv run cyt proxy --upstream https://api.anthropic.com --upstream-kind anthropic
# Or
uv run cyt proxy --upstream https://api.openai.com --upstream-kind openai
```

Default listen port: **8834** (from bundled `defaults.yaml` or `~/.config/cyt/config.yaml`).

<details>
<summary><strong>Configure the proxy (optional)</strong></summary>
Interactive wizard (writes `~/.config/cyt/config.yaml` and optionally `~/.config/cyt/.env`):

```bash
uv run cyt setup
```

Or edit `~/.config/cyt/config.yaml` manually — see [CONFIG.md](CONFIG.md).

Without `cyt setup`, the proxy uses the **default BM25 pipeline** — local pruning with no
remote API keys. Run `cyt setup` to configure rerank/llm pruners and full cost tracking.

</details>

### 3. Run the the Agent

Examples for **Codex** & **Claude Code** are in [./examples/agents](./examples/agents) dir.

### 4. View pruning stats savings

```bash
uv run cyt stats totals
uv run cyt stats summary --period day
uv run cyt stats events --limit 20

# Optional (recommended):
uv run cyt setup
```

Stats are stored in `~/.config/cyt/stats.db` by default.

---

## FAQ

<details>
<summary><strong>Doesn't pruning burn more tokens than it saves?</strong></summary>

The default is BM25 algorithm running locally on your computer it is free.
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
the request is unchanged. Codex agent has an efficient tool use and CYT saves less tokens.

How much you save overall depends on:

- **How many tools you have** — more MCP servers mean a larger share of the request is tool
  schemas. We do not recommend using CYT below 50 tools.
- **Which pruning policy you use** — see [Pruning policies](CONFIG.md#configuration).

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
<summary><strong>Is my provider/model supported?</strong></summary>

CYT's **pruner models** (the cheap reranker and LLM that decide which tools to keep) call providers through [LiteLLM](https://docs.litellm.ai/docs/providers).
If LiteLLM supports your provider and model, you can use them in CYT.

When you run `cyt setup` and add a pruner model, you'll be prompted for:

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

**Fix:** upgrade to a `cyt` build that streams upstream bytes unchanged (`aiter_raw` pass-through).
After upgrading, verify:

```bash
curl --raw -sS -D - -o /tmp/cyt-msg.body \
  -H 'Accept-Encoding: gzip' \
  ... # your POST to http://127.0.0.1:8834/anthropic/v1/messages
head -c 4 /tmp/cyt-msg.body | xxd   # should show 1f8b when header says gzip
```

**Also check:** `ANTHROPIC_BASE_URL` must use **`http://`** for the default plain-HTTP server,
e.g. `http://localhost:8834/anthropic`. Using **`https://`** against `cyt proxy` (without TLS/`http2.serve`)
causes uvicorn’s `Invalid HTTP request received` and broken API calls.

</details>

<details>
<summary><strong>Uvicorn logs Invalid HTTP request received</strong></summary>

`cyt proxy` listens for **HTTP/1.1** on the configured port (default **8834**).
This warning almost always means a client connected with the wrong protocol:

- **`https://localhost:8834`** while the proxy is plain HTTP → TLS handshake bytes, not HTTP
- HTTP/2 prior knowledge to uvicorn (use `http2.serve` + TLS certs only if you intend HTTPS)

Use `http://localhost:8834/anthropic` unless you have enabled Hypercorn TLS in config.

</details>

<details>
<summary><strong>Should I use .env</strong></summary>

We strongly recommend using password vaults like macOS KeyChain

```bash
# Store key in secure vault
security add-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w "sk-..."  # macOS

# Now you can access the key like this:
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
```

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

[quick-start-shield]: https://img.shields.io/badge/Quick_Start-5_min-blue?style=for-the-badge
[license-badge-shield]: https://img.shields.io/badge/License-Apache_2.0-yellow?style=for-the-badge
[version-shield]: https://img.shields.io/github/v/release/qdrddr/clear-your-tools?style=flat-square&label=version&color=4385BE&logoColor=white
[release-link]: https://github.com/qdrddr/clear-your-tools/releases
[npm-sdk-version-shield]: https://img.shields.io/npm/v/cyt-indexer-sdk?logo=npm&color=3178C6&logoColor=white
[npm-sdk-downloads-shield]: https://img.shields.io/npm/dm/cyt-indexer-sdk?logo=npm&color=3178C6&logoColor=white
[npm-link]: https://www.npmjs.com/package/cyt-indexer-sdk
[pypi-cyt-version-shield]: https://img.shields.io/pypi/v/clear-your-tools?logo=pypi&logoColor=white&color=2E8B57
[pypi-cyt-downloads-shield]: https://img.shields.io/pypi/dm/clear-your-tools?logo=pypi&logoColor=white&color=2E8B57
[pypi-cyt-link]: https://pypi.org/project/clear-your-tools/
[pypi-sdk-version-shield]: https://img.shields.io/pypi/v/cyt-indexer-sdk?logo=pypi&logoColor=white&color=4EAA25
[pypi-sdk-downloads-shield]: https://img.shields.io/pypi/dm/cyt-indexer-sdk?logo=pypi&logoColor=white&color=4EAA25
[pypi-sdk-link]: https://pypi.org/project/cyt-indexer-sdk/
[rust-version-shield]: https://img.shields.io/crates/v/cyt-indexer?logo=rust&color=e6522c&logoColor=white
[rust-downloads-shield]: https://img.shields.io/crates/d/cyt-indexer?logo=rust&color=e6522c&logoColor=white
[rust-link]: https://crates.io/crates/cyt-indexer
[shell-shield]: https://img.shields.io/badge/-Shell-4EAA25?logo=gnu-bash&logoColor=white
[python-tech-shield]: https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white
[typescript-shield]: https://img.shields.io/badge/-TypeScript-3178C6?logo=typescript&logoColor=white
[rust-tech-shield]: https://img.shields.io/badge/-Rust-3776AB?logo=rust&logoColor=white
[windows-shield]: https://img.shields.io/badge/Windows-supported-0078D6?logo=windows&logoColor=white
[macos-shield]: https://img.shields.io/badge/macOS-supported-000000?logo=apple&logoColor=white
[linux-shield]: https://img.shields.io/badge/Linux-supported-FCC624?logo=linux&logoColor=black
[license-link]: LICENSE
[telemetry-shield]: https://img.shields.io/badge/No_Telemetry-none-green?style=for-the-badge
[discord-shield]: https://img.shields.io/badge/Discord-Join-5865F2?logo=discord&logoColor=white
[discord-link]: https://discord.com/invite/FhACaAAW9C
