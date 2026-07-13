# Development

Guide for working on Clear Your Tools from a source checkout or integrating the Python package.

## Prerequisites

Install on your machine before the setup steps below:

| Tool | Used for |
| ---- | -------- |
| [`uv`](https://docs.astral.sh/uv/) | Python deps, `prek`, `uv run` |
| Python **3.13+** | App and SDK (see [`pyproject.toml`](pyproject.toml)) |
| **Rust** (stable) | Editable `cyt-indexer-sdk` (maturin), `cargo` prek hooks, TypeScript native build |
| **Go 1.25+** | [`sdk/go/`](sdk/go/) cgo bindings; pre-commit uses `go tool` from `sdk/go/go.mod` |
| **Node.js ≥20** | TypeScript SDK build and prek hooks under `sdk/typescript/` |
| **`ast-grep`** CLI | `ast-grep` / `ast-scan` prek hooks ([install](https://ast-grep.github.io/guide/quick-start.html#install)) |
| **C toolchain** | [`sdk/c/`](sdk/c/) examples and cgo; same as Rust FFI build |
| **clang-format, clang-tidy, cppcheck, cpplint** | C SDK pre-commit hooks (macOS: `brew install llvm cppcheck cpplint`) |

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
task ci                     # Python checks mirroring CI (sync, ast-grep, import checks, ruff, mypy, pytest, build)
```

TypeScript-only hooks: `task -d sdk prek` or `cd sdk/typescript && npm test`.

Skip one hook: `SKIP=<hook-id> git commit …` (for example `SKIP=pytest`). Registry E2E against live packages: [`sdk/e2e/scripts/run-local.sh`](sdk/e2e/scripts/run-local.sh).

### C SDK (for clang-tidy pre-commit hook)

Generate `compile_commands.json` once after clone (re-run when `sdk/c/CMakeLists.txt` changes):

```bash
cmake -S sdk/c -B sdk/c/build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_BUILD_TYPE=Release
```

Build the shared library and C examples:

```bash
bash sdk/c/scripts/build-c-lib.sh
cmake --build sdk/c/build
ctest --test-dir sdk/c/build --output-on-failure
```

### Go SDK (for pre-commit hooks)

Requires **Go 1.25+** with **CGO enabled**. Install pinned dev tools into the module:

```bash
bash sdk/c/scripts/build-c-lib.sh
cd sdk/go && go mod tidy
go tool gofumpt -version
go test ./...
```

### Secret scanning

Pre-commit runs layered scanners: `detect-secrets` (baseline in [`.secrets.baseline`](.secrets.baseline)),
[gitleaks](.gitleaks.toml), truffleHog, and [talisman](.talismanrc). After intentional false positives, update
the relevant allowlist or baseline:

```bash
detect-secrets scan --baseline .secrets.baseline
detect-secrets audit .secrets.baseline
```

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
├── sdk/                         # independently published SDKs (not bundled in clear-your-tools wheel)
│   ├── rust/cyt-indexer/        # crates.io: cyt-indexer
│   ├── python/                  # PyPI: cyt-indexer-sdk (import name: cyt_indexer)
│   ├── typescript/              # npm: cyt-indexer-sdk
│   ├── c/                       # libcyt_indexer (GitHub releases)
│   └── go/                      # github.com/qdrddr/clear-your-tools/sdk/go
└── src/
    ├── cyt/                     # installable package (Clear Your Tools)
    │   ├── config/              # load_config, defaults.yaml
    │   ├── common/              # path_constants, runtime_constants, token_usage, pricing
    │   ├── indexer/             # adapter over cyt-indexer-sdk (+ app helpers)
    │   ├── pruners/             # llm, rerank, policies
    │   └── proxy/               # transport, reverse, anthropic, stats, cli
    ├── cyt_core/                # headless core (no import side effects)
    └── cyt_client/              # lightweight client helpers
```

## Package boundaries

The monorepo builds several **independently publishable** artifacts. The main Python app must not
couple to `sdk/python` source paths — it depends on the installed **`cyt-indexer-sdk`** package
(PyPI module name: `cyt_indexer`).

```text
sdk/rust/cyt-indexer  →  cyt-indexer-sdk (PyPI / npm / libcyt_indexer / Go cgo)
                              ↓ declared dependency (cyt-indexer-sdk==X.Y.Z)
clear-your-tools (PyPI) →  cyt, cyt_core, cyt_client
```

### Import rules (main app)

| Allowed | Not allowed |
| ------- | ----------- |
| `from cyt.indexer import ...` | `from cyt_indexer import ...` outside adapter modules |
| `from cyt_core.indexer import ...` | Adding `sdk/python` to `PYTHONPATH` or `sys.path` |
| `from cyt_core.types import PolicyContext` | Importing `sdk/python/src/...` by filesystem path |

**Adapter modules** (the only places that may import `cyt_indexer` directly):

- `src/cyt/indexer/**` — app-facing facade (`cache`, `documents`, `policies`, `build`, …)
- `src/cyt_core/indexer/**` — headless core facade
- `src/cyt_core/bootstrap.py` — SDK runtime configuration
- `src/cyt_core/types/**` — type aliases over the SDK

Application code (proxy, pruners, tools, skills, config, …) must use `cyt.indexer` or `cyt_core`
facades. Tests may import `cyt_indexer` only in documented SDK parity tests (for example
`src/tests/test_removed_chunks.py`).

Enforcement:

- `src/tests/test_import_boundaries.py` — AST check on every CI run (via pytest)
- `.ast-grep/rules/python-no-direct-cyt-indexer-import.yml` — `ast-grep scan` in CI and prek
- `scripts/check_agent_imports.py` and `scripts/check_cyt_client_imports.py` — import smoke checks in CI and prek

### Dev vs production dependency resolution

| Context | `cyt-indexer-sdk` source |
| ------- | ------------------------ |
| Monorepo dev (`uv sync`) | Editable path: `[tool.uv.sources] cyt-indexer-sdk = { path = "sdk/python", editable = true }` |
| Published `clear-your-tools` wheel | PyPI pin: `cyt-indexer-sdk==X.Y.Z` in `[project.dependencies]` |

Local workflow: [`scripts/local-dev.sh`](scripts/local-dev.sh) (`app-setup`, `sdk-python`, `app-verify`).
Registry isolation smoke: `./scripts/local-dev.sh simulate-registry` (installs built wheels in a temp venv).
Published-package E2E: [`sdk/e2e/README.md`](sdk/e2e/README.md).

Optional publish check: `CYT_ENFORCE_INSTALLED_SDK=1 uv run pytest src/tests/test_import_boundaries.py`
asserts `cyt_indexer` resolves from site-packages, not `sdk/python`.

## Library usage

```python
from cyt.indexer import CatalogIndex, build_catalog_index, load_catalog, retrieve_tools
from cyt.pruners import rerank_catalog_dict, llm_catalog_dict
from cyt.pruners.policies import configure_policies_from_config
from cyt.proxy.reverse import create_app  # requires clear-your-tools[proxy]
```

Advanced (not re-exported from `cyt.indexer`):

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

| Section                                                     | Purpose                                                    |
| ----------------------------------------------------------- | ---------------------------------------------------------- |
| `pruning.tools.policy.system_tool` / `mcp_tool`             | Default pruning behavior for system vs MCP tools           |
| `pruning.tools.policy.minimum_tools`                        | Tool-count threshold for rerank/llm stages                 |
| `pruning.tools.pipelines.rerank.model_nick`                 | Reranker catalog nick for the `rerank` stage               |
| `pruning.tools.pipelines.llm.model_nick`                    | LLM pruner catalog nick for the `llm` stage                |
| `pruning.tools.pipelines.bm25.index_dir`                    | BM25 index directory                                       |
| `pruning.tools.sequence`                                    | Ordered list of stages: `rerank`, `llm`, `bm25`            |
| `pruning.tools.policy.per_tool`                             | Per-tool policy overrides                                  |
| `models.providers[]` + model `provider_nick`                | Provider credentials (legacy inline fields still work)     |
| `models.rerankers` / `models.llm`                           | Remote model definitions and API keys                      |
| `network.proxy.reverse`                                     | Listen port, upstream URLs, HTTP/2, TLS                    |
| `stats`                                                     | Stats DB path, optional full tool JSON storage             |

Legacy paths (`pruning.pipeline`, `pruning.policy`, `pruning.<stage>`, …) resolve via
[`src/cyt/config/legacy.py`](src/cyt/config/legacy.py).

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
