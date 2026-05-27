# Tool Attention — Reference Implementation

Companion code for the paper
**"Tool Attention Is All You Need: Dynamic Tool Gating and Lazy Schema Loading for Eliminating the MCP/Tools Tax
inScalable Agentic Workflows"**
(Anuj Sadani, 2026). Published on arXiv: [arxiv.org/abs/2604.21816](https://arxiv.org/abs/2604.21816).
See [`paper.md`](paper.md) for the Markdown version or [`latex/paper.pdf`](latex/paper.pdf) for the rendered PDF.

Tool Attention is a drop-in middleware layer for LLM agents that eliminates
the *MCP Tax* — the 10k–60k tokens of tool-schema overhead that stateless
MCP injection imposes on every conversational turn. It combines:

1. an **Intent–Schema Overlap (ISO)** score over sentence embeddings,
2. a **state-aware gating function** enforcing preconditions and scopes, and
3. a **two-phase lazy schema loader** (summary pool + on-demand full schemas).

On a 120-tool synthetic MCP catalog, the reference implementation measures a
**98.6% reduction in the always-resident summary pool** and a **91.9%
reduction in the per-turn marginal schema cost** versus naive full-schema
injection, with steady-state cost dominated by the cache-amortized Phase-2
payload.

---

## Repository layout

```shell
.
├── paper.md                     # full preprint (Markdown, LaTeX math)
├── latex/
│   ├── paper.tex                # arXiv-style LaTeX source
│   ├── references.bib           # 35-entry bibliography
│   └── paper.pdf                # 19-page rendered preprint
└── src/
    ├── vector_store.py          # FAISS-backed tool-summary index
    ├── intent_router.py         # ISO router + state-aware gate
    ├── lazy_loader.py           # LRU-cached full-schema loader
    ├── tool_attention.py        # before_model / after_model middleware
    ├── build_catalog.py         # generates the 120-tool synthetic testbed
    └── benchmark.py             # token-counting harness
```

---

## Installation

Requires Python 3.11+ (see [`pyproject.toml`](pyproject.toml)).

Dependencies are managed with [`uv`](https://docs.astral.sh/uv/). From the repository root run:

```bash
uv sync
```

The first run will download the
[`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
encoder (~90 MB).

### Self-signed TLS certificates (HTTP/2 proxy)

HTTP/2 serving requires TLS. Generate a local cert/key pair into `src/crt/` (gitignored):

```bash
mkdir -p src/crt
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout src/crt/key.pem \
  -out src/crt/cert.pem \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
```

Point the proxy at them in `src/config.yaml` (uncomment `network.proxy.http2`) or pass CLI flags:

```bash
uv run src/proxy.py serve --http2-serve \
  --ssl-keyfile src/crt/key.pem \
  --ssl-certfile src/crt/cert.pem
```

Ensure to add the self-signed cert.pem to your system so it could be trusted.
In macOS > Keychain Access > System > drag & drop the cert.pem > open localhost added just now > expand "Trust" Dropdown > in the "When Using this certificate" select "Always Trust" and close.

---

## Quick start: reproduce the token-reduction numbers

`pyproject.toml` lives in the repository root, while the scripts are in `src/`.
You can invoke them directly from the root with `uv run`:

```bash
uv run src/build_catalog.py   # creates src/catalog/tools.json and src/catalog/schemas/*.json
uv run src/benchmark.py       # prints the reduction table
```

Or change into `src/` and run `uv run build_catalog.py` / `uv run benchmark.py`
— `uv` discovers the project root automatically.

Expected output (seed 42, 120 tools, 7 queries):

```shell
| Method                              | tokens/turn |   reduction |
|-------------------------------------|------------:|------------:|
| B1 Naive Full-Schema                |      57,452 |        0.0% |
| B3 Simple Retrieval (top-k schemas) |       5,390 |       90.6% |
| Tool Attention: Phase-1 only        |         787 |       98.6% |
| Tool Attention: Phase-2 only        |       4,672 |       91.9% |
| Tool Attention: first turn (P1+P2)  |       5,459 |       90.5% |
```

Under prompt caching the steady-state per-turn cost is dominated by Phase-2
(the full schemas for the top-\(k\) active tools); Phase-1 is cached after
the first turn.

---

## Minimal library usage

All snippets below assume execution inside the `uv` managed environment
(e.g., run `uv run python` from the `src/` directory):

```python
from pathlib import Path
import tiktoken
from sentence_transformers import SentenceTransformer

from vector_store import ToolVectorStore
from intent_router import IntentRouter
from lazy_loader import LazySchemaLoader
from tool_attention import ToolAttention

enc = tiktoken.get_encoding("cl100k_base")
count = lambda s: len(enc.encode(s))

encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

tools = [
    {"id": "github__list_open_prs",
     "summary": "List open GitHub pull requests filtered by label and assignee."},
    {"id": "slack__search",
     "summary": "Search Slack messages by channel, author, and time range."},
    # ... hundreds more
]

store = ToolVectorStore(dim=384)
store.add_tools(tools, encoder)

loader = LazySchemaLoader(registry_path=Path("catalog/schemas"))
router = IntentRouter(store=store, encoder=encoder, threshold=0.28, top_k=10)
ta     = ToolAttention(store, loader, router, token_counter=count)

# Per-turn middleware call
result = ta.before_model("list open PRs labeled `bug` in the auth repo")
print(result.active_ids)            # routed tools for this turn
print(result.phase1_tokens, result.phase2_tokens)
```

### State-aware gating

`IntentRouter.route(query, precondition_check=...)` takes an optional
predicate that receives a tool_id and returns `True` if the agent's current
state satisfies the tool's preconditions (auth scopes, workflow milestones,
etc.):

```python
def allowed(tool_id: str) -> bool:
    if tool_id.startswith("github__") and not state.authenticated("github:write"):
        return False
    return True

result = ta.before_model(query, precondition_check=allowed)
```

### Hallucination rejection gate

After the model returns a tool call, wrap it through `after_model` to
reject any call targeting a tool whose schema was not promoted this turn:

```python
err = ta.after_model(result.active_ids, requested_tool=model_output.tool_name)
if err:
    # Return `err` to the model as a structured error; it will retry.
    pass
```

## FAQ

### Doesn't pruning burn more tokens than it saves?

The reranker and LLM used for pruning are **much cheaper per token** than the main model (e.g. Claude Sonnet). You may spend extra tokens on pruning, but they cost a fraction of what you save on the main request.

**Example pricing (input tokens):**

| Model | Cost per 1M input tokens |
|-------|-------------------------|
| Claude Sonnet 4.6 | $3.00 |
| Inception Mercury 2 | $0.25 |
| Qwen-Reranker-8B | $0.050 |

Mercury 2 also returns only the IDs of tools to keep, so its output stays extreamly small. While Rerankers do not count output tokens and usually much cheaper vs. strong LLM.

**Rule of thumb:** saving 1M Sonnet input tokens is still worthwhile even if pruning uses up to ~10M Mercury tokens — roughly a 1:10 cost ratio. While Reranker has 1:60 cost ratio.

In practice, pruning usually adds a modest overhead. Worst case (no tools pruned), you might pay ~$3.30 instead of $3.00. With typical pruning (40–95% of tool tokens removed), tool-schema cost drops from ~$3.00 to roughly **$0.15–$1.80**, plus ~$0.30 for pruning with Mercury Model — about **$0.45–$2.10 total** for the tool pruning. That is roughly **30–85% savings on tool-related cost**, depending on how aggressive your policy is set.

### Why don't I see 30–85% savings on my total request?

Those numbers apply to **tool schemas only**, not the full prompt (system message, conversation history, user message, etc.). This app is only prunes tools based on the user request, the rest of the request reamins unchanged.

How much you save overall depends on:

- **How many tools you have** — more MCP servers and tools mean a larger share of the request is tool schemas.
- **Which pruning policy you use** — see the next section.

To estimate total savings on a real request:

```bash
uv run count_request_tokens.py \
  --tool-savings-percent 85 \
  --requestfile temp_example_claude_call.json
```

With ~100 tools and `prune_all`, expect **~85-85% savings on tool tokens** and typically more than **~30% savings on the full request**.

### What are the pruning policies, and how do I maximize savings?

There are two tool categories:

| Category | Default Policy | Examples | Typical prefix |
|----------|----------|----------|----------------|
| **System tools** | `prune_optional` | `Read`, `Write`, `Agent` | (no `mcp__` prefix) |
| **MCP tools** | `prune_all` | Tools from MCP servers | `mcp__…` |

Set defaults in `config.yaml` under `defaults.system_tool_policy` and `defaults.mcp_tool_policy`.

**Policy options:**

| Policy | What it does |
|--------|--------------|
| `always_include` | No pruning — the tool and full tool schema every turn included. |
| `prune_optional` | Keep the tool, but drop optional properties that look irrelevant to the query. Required properties are always kept. |
| `prune_all` | Most aggressive — entire tools can be removed if they look irrelevant. If a tool is kept, its required properties are always included; optional ones are trimmed when irrelevant to the query. |

`prune_all` saves the most tokens. With ~100 tools, expect up to **~95% reduction in tool-schema tokens**.

### Can I override pruning for specific tools?

Yes. Per-tool policies in `config.yaml` override the system/MCP defaults:

```yaml
pruning:
  per_tool:
    mcp__hedl__hedl_convert_from: prune_optional   # this tool always inc;luded; trim optional fields if irrelevant to the user query
    Agent: prune_optional
    mcp__hedl__batch: prune_all                    # may be removed entirely
    mcp__fff__multi_grep: always_include           # never prune, entier tool and its full definitions always remains unchenged
```

- **`always_include`** — tool is never pruned (no savings)
- **`prune_optional`** — tool is always included; only irrelevant optional properties are removed (moderate savings about 45%)
- **`prune_all`** — tool may be dropped entirely when irrelevant (most aggressive, saves about 95%)

### Where can I see how many tools and parameters an MCP server has?

A very popular [Fetch](https://mcpmarket.com/server/fetch) MCP server is a good example. On its **Tools** tab you can see 4 tools, each with 4 parameters (1 required, 3 optional) — 16 parameters in total.

If the user asks to fetch the Markdown of a webpage, `prune_all` typically keeps only the **Fetch Markdown** tool: its required parameter plus any optional parameters that look relevant. That shrinks the payload from 4 tools and 16 parameters to roughly 1 tool and 1–2 parameters. Unrelated tools (for example, **Read file**) are dropped entirely.


## Proxy

Entry point: `uv run src/proxy.py serve`

| Port | Mode | Client setting |
|------|------|----------------|
| **8834** | Reverse (path-based) | `ANTHROPIC_BASE_URL=http://localhost:8834/anthropic` |
| **8835** | Forward MITM | `"http.proxy": "http://127.0.0.1:8835"` (any HTTP-proxy client) |

### Reverse proxy (Claude Code)

No TLS:

```shell
uv run src/proxy.py serve --port 8834
```

HTTP/2 serve (requires TLS on reverse port):

```shell
uv pip install h2 'hypercorn[h2]'
openssl req -x509 -nodes -days 365 -newkey rsa:4096 \
  -keyout src/crt/key.pem -out src/crt/cert.pem \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

uv run src/proxy.py serve --http2-serve \
  --ssl-keyfile src/crt/key.pem \
  --ssl-certfile src/crt/cert.pem \
  --port 8834
```

Claude Code:

```shell
export ANTHROPIC_BASE_URL="http://localhost:8834/anthropic"
export OPENROUTER_API_KEY="sk-..."
export ANTHROPIC_AUTH_TOKEN=${OPENROUTER_API_KEY}
$HOME/.local/bin/claude --model haiku 'say hi' -p
```

Reverse debug (dry-run, no upstream): `uv run src/proxy.py serve --debug --port 8834`

### Forward MITM proxy

The forward proxy terminates TLS (MITM) so decrypted request/response bodies can be logged and transformed. It requires a **separate CA** from the reverse TLS cert (`cert.pem` is for localhost reverse only).

Generate MITM CA (once):

```shell
mkdir -p src/crt
openssl req -x509 -newkey rsa:4096 -nodes -days 3650 \
  -keyout src/crt/mitm-ca-key.pem -out src/crt/mitm-ca.pem \
  -subj "/CN=ToolAttention MITM CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0"
```

Trust the CA on your machine (macOS example):

```shell
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain src/crt/mitm-ca.pem
```

Configure any HTTP-proxy client:

```json
{
  "http.proxy": "http://127.0.0.1:8835",
  "http.proxyStrictSSL": false
}
```

Forward debug (append decrypted bodies to `forward.log` while forwarding):

```shell
uv run src/proxy.py serve --debug
```

Disable forward proxy: `uv run src/proxy.py serve --no-forward`

Test:

```shell
./scripts/test_forward_proxy.sh
```
