# Development

Guide for working on Clear Your Tools from a source checkout or integrating the Python package.

## Prerequisites

Install on your machine before the setup steps below:

| Tool | Used for |
| ---- | -------- |
| [`uv`](https://docs.astral.sh/uv/) | Python deps, `prek`, `uv run` |
| Python **3.13+** | App and SDK (see [`pyproject.toml`](pyproject.toml)) |
| **Rust** (stable) | Editable `cyt-indexer-sdk` (maturin), `cargo` prek hooks, TypeScript native build |
| **Node.js ≥20** | TypeScript SDK build and prek hooks under `sdk/typescript/` |
| **`ast-grep`** CLI | `ast-grep` / `ast-scan` prek hooks ([install](https://ast-grep.github.io/guide/quick-start.html#install)) |

Registry E2E (published crates/PyPI/npm only) needs `cargo`, `npm`, and network access — see [`sdk/e2e/README.md`](sdk/e2e/README.md).

## Setup

Dependencies are managed with [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync --all-extras
```

### TypeScript SDK (for `prek run -a`)

`native.cjs`, `native.d.ts`, `*.node`, and `dist/` under `sdk/typescript/` are **gitignored** — they are built from Rust,
not copied from another checkout. After clone:

```bash
cd sdk/typescript
npm ci
npm run build   # needs Node ≥20 and a Rust toolchain
```

Run this before the first full `prek run -a` (typecheck runs before the `typescript-build` hook).
Python-only work needs only `uv sync`.

From PyPI (proxy + pruners):

```bash
uv pip install 'clear-your-tools[all]'
```

Copy API keys (or use `~/.config/cyt/.env`):

```bash
cp .env.example .env
# Edit .env — at minimum DEEPINFRA_API_KEY (reranker) and OPENROUTER_API_KEY (upstream + optional LLM stage)
```

## Checks

After setup, run hooks locally before pushing:

```bash
uv run prek run -a          # all pre-commit hooks (build TS SDK first; see above)
task ci                     # Python checks mirroring CI (sync, ruff, mypy, pytest, build)
```

TypeScript-only hooks: `task -d sdk prek` or `cd sdk/typescript && npm test`.

Skip one hook: `SKIP=<hook-id> git commit …` (for example `SKIP=pytest`). Registry E2E against live packages: [`sdk/e2e/scripts/run-local.sh`](sdk/e2e/scripts/run-local.sh).

## Run from a checkout

```bash
uv run cyt proxy --port 8834
```

Other CLI entry points work the same way with `uv run` (for example `uv run cyt stats totals`).

## Repository layout

```text
.
├── README.md
├── DEV.md
├── LIMITATIONS.md
├── pyproject.toml
├── count_request_tokens.py      # estimate savings on a captured request JSON
└── src/
    └── cyt/                       # installable package (Clear Your Tools)
        ├── config/                # load_config, defaults.yaml
        ├── common/                # path_constants, runtime_constants, token_usage, pricing
        ├── indexer/               # build, retrieve, catalog_io, tokens (tiktoken)
        ├── pruners/               # llm, rerank, policies
        └── proxy/                 # transport, reverse, anthropic, stats, cli
```

## Library usage

```python
from cyt.indexer import CatalogIndex, build_catalog_index, load_catalog, retrieve_tools
from cyt.pruners import rerank_catalog_dict, llm_catalog_dict
from cyt.pruners.policies import configure_policies_from_config
from cyt.proxy.reverse import create_app  # requires clear-your-tools[proxy]
```

Advanced (submodules — not re-exported from `cyt.indexer`):

```python
from cyt.indexer.catalog_io import CatalogBuilder, write_catalog_index
from cyt.indexer.tokens import count_tokens, count_json_tokens, compact_json
from cyt.indexer.build import collect_enums, prepare_tool_entry, prepare_system_tool_entry
from cyt.common.path_constants import DECOMPOSED_PREFIX
```

## Configuration reference

Main config file: `config.yaml` in the working directory, or
[`~/.config/cyt/config.yaml`](~/.config/cyt/config.yaml) (created on first run).
Run `cyt setup` for an interactive wizard that writes the user config and optional `~/.config/cyt/.env`.
Bundled defaults ship in the package as `cyt.config.defaults.yaml`.

User-facing guides (pricing overrides, `rerank` → `llm` pipeline, OpenRouter vs OpenAI pruning models):
[README.md — Configuration](README.md#configuration).

| Section                                                   | Purpose                                                  |
| --------------------------------------------------------- | -------------------------------------------------------- |
| `defaults.system_tool_policy` / `mcp_tool_policy`         | Default pruning behavior for system vs MCP tools         |
| `defaults.remote.reranking_model_nick` / `llm_model_nick` | Model nicknames for pruning stages                       |
| `pruning.pipeline`                                        | Ordered list of stages: `rerank`, `llm`                  |
| `pruning.per_tool`                                        | Per-tool policy overrides                                |
| `models.rerankers` / `models.llm`                         | Remote model definitions, API keys, minimum tool counts  |
| `network.proxy.reverse`                                   | Listen port, upstream URLs, HTTP/2, TLS                  |
| `stats`                                                   | Stats DB path, optional full tool JSON storage           |

Environment variables (see [`.env.example`](.env.example)):

- `DEEPINFRA_API_KEY` — reranker stage
- `OPENROUTER_API_KEY` — upstream forwarding and optional LLM stage

## Estimate savings on a captured request

Use `count_request_tokens.py` on a JSON snapshot from debug dry-run (see [debug/](debug/)):

```bash
uv run count_request_tokens.py \
  --tool-savings-percent 85 \
  --requestfile temp_example_claude_call.json
```

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
cyt proxy --http2-serve \
  --ssl-keyfile src/crt/key.pem \
  --ssl-certfile src/crt/cert.pem \
  --port 8834
```

TLS settings can also live in `config.yaml` under `network.proxy.reverse.http2.ssl`.
