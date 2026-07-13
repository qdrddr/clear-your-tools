# Registry end-to-end tests

Smoke tests that install **only published packages** from public registries—or, for Go/C, a **sparse GitHub tag
checkout**—not the active monorepo tree (unless `--workspace` is set).

| Harness | Source | Package |
| ------- | ------ | ------- |
| [`rust/`](rust/) | [crates.io](https://crates.io/crates/cyt-indexer) | `cyt-indexer` |
| [`python/`](python/) | [PyPI](https://pypi.org/project/cyt-indexer-sdk/) | `cyt-indexer-sdk` |
| [`typescript/`](typescript/) | [npm](https://www.npmjs.com/package/cyt-indexer-sdk) | `cyt-indexer-sdk` |
| [`clear-your-tools/`](clear-your-tools/) | [PyPI](https://pypi.org/project/clear-your-tools/) | `clear-your-tools` (pulls `cyt-indexer-sdk` transitively) |
| [`go/`](go/) | [GitHub tag](https://github.com/qdrddr/clear-your-tools/tags) | `github.com/qdrddr/clear-your-tools/sdk/go` |
| [`c/`](c/) | [GitHub tag](https://github.com/qdrddr/clear-your-tools/tags) | `sdk/c` + `libcyt_indexer` built from tagged crate |

## CI

Workflow: [`.github/workflows/e2e-published-sdk.yml`](../../.github/workflows/e2e-published-sdk.yml)

Runs after **`publish-crates.yml`** succeeds (`workflow_run`), reads semver from the crates publish artifact, polls
each registry until that version is available, then runs the harness tests.

Complements:

- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — source-tree tests on PR/push
- [`.github/workflows/sdk-c-go.yml`](../../.github/workflows/sdk-c-go.yml) — monorepo C/Go matrix on PR/push
- Publish chain — push tag `vX.Y.Z` → `publish-crates.yml` → (`publish-pypi-sdk.yml`, `publish-npm-sdk.yml`,
`e2e-published-sdk.yml` via `workflow_run`) → `publish-pypi.yml` after SDK PyPI

## Local run

Use [`scripts/run-local.sh`](scripts/run-local.sh) after a release is on registries (and tagged on GitHub for Go/C).
It renders manifests, optionally polls registries/tags, and runs the harness tests.

```bash
# Workspace version from sdk/rust/cyt-indexer/Cargo.toml, all six targets
./sdk/e2e/scripts/run-local.sh

# Explicit version (packages must already be published; tag must exist for go/c)
./sdk/e2e/scripts/run-local.sh 0.1.10

# One target, skip registry polling when you know the version is live
./sdk/e2e/scripts/run-local.sh --skip-wait python
./sdk/e2e/scripts/run-local.sh v0.1.10 rust typescript

# Go/C against current monorepo (unreleased local work)
./sdk/e2e/scripts/run-local.sh --workspace --skip-wait go c
```

Targets: `rust`, `python`, `typescript`, `clear-your-tools`, `go`, `c`, `all` (default).

**Prerequisites:** `cargo`, `go` 1.25+ with CGO, `cmake`, `uv` (Python 3.13+), `node`/`npm`, network access to public
registries and GitHub.

### CI-style run

For parity with the GitHub workflow (e.g. in automation), set the version explicitly and use
[`scripts/run-all.sh`](scripts/run-all.sh):

```bash
export CYT_RELEASE_VERSION=0.1.10   # or TAG=v0.1.10
./sdk/e2e/scripts/run-all.sh
```

Low-level registry polling: [`scripts/wait-registry.sh`](scripts/wait-registry.sh) targets `crate`, `pypi-sdk`,
`pypi-app`, `npm`, `tag`. Set `SKIP_REGISTRY_WAIT=1` to skip waits in `run-local.sh`, `run-all.sh`, or `run-target.sh`.

## Go/C staging

Go and C harnesses clone tag `vX.Y.Z` into a temp directory (`CYT_E2E_STAGING`), build `libcyt_indexer` from the tagged
Rust crate, then run isolated tests:

- Go: rendered `go.mod` uses a `replace` directive to the staging `sdk/go` tree (cgo links `../../target/...` from there).
- C: CMake links the staging shared library and header under `sdk/c/include/`.

Scripts: [`prepare-release-checkout.sh`](scripts/prepare-release-checkout.sh), [`build-staging-c-lib.sh`](scripts/build-staging-c-lib.sh).

## Manifest templates

Version pins live in `*.in` templates (`@CYT_RELEASE_VERSION@` placeholder; Go also uses `@CYT_E2E_STAGING@`).
`render-manifests.sh` writes gitignored `Cargo.toml`, `pyproject.toml`, `package.json`, and `go.mod` files so PRs do
not churn release versions.

## Scripts

| Script | Purpose |
| ------ | ------- |
| [`scripts/run-local.sh`](scripts/run-local.sh) | **Local entry point** — defaults, per-target runs, `--skip-wait`, `--workspace` |
| [`scripts/run-all.sh`](scripts/run-all.sh) | Run all six targets (CI-style; requires `CYT_RELEASE_VERSION` or `TAG`) |
| [`scripts/run-target.sh`](scripts/run-target.sh) | Run one harness (`rust`, `python`, `typescript`, `clear-your-tools`, `go`, `c`) |
| [`scripts/prepare-release-checkout.sh`](scripts/prepare-release-checkout.sh) | Sparse clone of tag `vX.Y.Z` for Go/C |
| [`scripts/build-staging-c-lib.sh`](scripts/build-staging-c-lib.sh) | Build `libcyt_indexer` inside staging checkout |
| [`scripts/parse-version.sh`](scripts/parse-version.sh) | Parse semver from `TAG` or `CYT_RELEASE_VERSION` |
| [`scripts/render-manifests.sh`](scripts/render-manifests.sh) | Generate manifests from `.in` templates |
| [`scripts/wait-registry.sh`](scripts/wait-registry.sh) | Poll registry/tag until version is installable |
| [`scripts/uv-sync-with-retry.sh`](scripts/uv-sync-with-retry.sh) | Retry `uv sync` while PyPI index propagates (`UV_SYNC_MAX_ATTEMPTS`, `UV_SYNC_RETRY_SECS`) |

## Shared fixtures

Language-neutral fixtures under [`fixtures/`](fixtures/) support BM25, token, and cohesion smoke tests:

| File | Purpose |
| ---- | ------- |
| `fixtures/bm25_catalog.json` | Small tool catalog for BM25 ranking smoke |
| `fixtures/cohesion_sample.md` | Markdown section for chunk coverage invariant |
| `fixtures/cohesion_config.json` | Word-mode cohesion config (`chunk_size: 2048`, `token_counter: tiktoken`) |

Rust, Python, TypeScript, Go, and C harnesses can exercise `count_tokens`, `bm25_score_catalog`, and
`bm25_cohesion_chunk` against these fixtures after release (workspace mode for local runs).
