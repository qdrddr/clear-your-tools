# Registry end-to-end tests

Smoke tests that install **only published packages** from public registries—not the monorepo checkout or local native builds.

| Harness | Registry | Package |
| ------- | -------- | ------- |
| [`rust/`](rust/) | [crates.io](https://crates.io/crates/cyt-indexer) | `cyt-indexer` |
| [`python/`](python/) | [PyPI](https://pypi.org/project/cyt-indexer-sdk/) | `cyt-indexer-sdk` |
| [`typescript/`](typescript/) | [npm](https://www.npmjs.com/package/cyt-indexer-sdk) | `cyt-indexer-sdk` |
| [`clear-your-tools/`](clear-your-tools/) | [PyPI](https://pypi.org/project/clear-your-tools/) | `clear-your-tools` (pulls `cyt-indexer-sdk` transitively) |

## CI

Workflow: [`.github/workflows/e2e-published-sdk.yml`](../../.github/workflows/e2e-published-sdk.yml)

Runs after **`publish-crates.yml`** succeeds (`workflow_run`), reads semver from the crates publish artifact, polls
each registry until that version is available, then runs the harness tests.

Complements:

- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — source-tree tests on PR/push
- Publish chain — push tag `vX.Y.Z` → `publish-crates.yml` → (`publish-pypi-sdk.yml`, `publish-npm-sdk.yml`,
`e2e-published-sdk.yml` via `workflow_run`) → `publish-pypi.yml` after SDK PyPI

## Local run

Use [`scripts/run-local.sh`](scripts/run-local.sh) after a release is on registries. It renders manifests, optionally
polls registries, and runs the harness tests.

```bash
# Workspace version from sdk/rust/cyt-indexer/Cargo.toml, all four targets
./sdk/e2e/scripts/run-local.sh

# Explicit version (packages must already be published)
./sdk/e2e/scripts/run-local.sh 0.1.10

# One target, skip registry polling when you know the version is live
./sdk/e2e/scripts/run-local.sh --skip-wait python
./sdk/e2e/scripts/run-local.sh v0.1.10 rust typescript
```

Targets: `rust`, `python`, `typescript`, `clear-your-tools`, `all` (default).

**Prerequisites:** `cargo`, `uv` (Python 3.13+), `node`/`npm`, network access to public registries.

### CI-style run

For parity with the GitHub workflow (e.g. in automation), set the version explicitly and use
[`scripts/run-all.sh`](scripts/run-all.sh):

```bash
export CYT_RELEASE_VERSION=0.1.10   # or TAG=v0.1.10
./sdk/e2e/scripts/run-all.sh
```

Low-level registry polling: [`scripts/wait-registry.sh`](scripts/wait-registry.sh) targets `crate`, `pypi-sdk`,
`pypi-app`, `npm`. Set `SKIP_REGISTRY_WAIT=1` to skip waits in `run-local.sh`, `run-all.sh`, or `run-target.sh`.

## Manifest templates

Version pins live in `*.in` templates (`@CYT_RELEASE_VERSION@` placeholder). `render-manifests.sh` writes gitignored
`Cargo.toml`, `pyproject.toml`, and `package.json` files so PRs do not churn release versions.

## Scripts

| Script | Purpose |
| ------ | ------- |
| [`scripts/run-local.sh`](scripts/run-local.sh) | **Local entry point** — defaults, per-target runs, `--skip-wait` |
| [`scripts/run-all.sh`](scripts/run-all.sh) | Run all four targets (CI-style; requires `CYT_RELEASE_VERSION` or `TAG`) |
| [`scripts/run-target.sh`](scripts/run-target.sh) | Run one harness (`rust`, `python`, `typescript`, `clear-your-tools`) |
| [`scripts/parse-version.sh`](scripts/parse-version.sh) | Parse semver from `TAG` or `CYT_RELEASE_VERSION` |
| [`scripts/render-manifests.sh`](scripts/render-manifests.sh) | Generate manifests from `.in` templates |
| [`scripts/wait-registry.sh`](scripts/wait-registry.sh) | Poll registry until version is installable |
| [`scripts/uv-sync-with-retry.sh`](scripts/uv-sync-with-retry.sh) | Retry `uv sync` while PyPI index propagates (`UV_SYNC_MAX_ATTEMPTS`, `UV_SYNC_RETRY_SECS`) |
