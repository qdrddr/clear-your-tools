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

Runs on **`release: published`**, parses semver from the release tag, polls each registry until that version is
available, then runs the harness tests.

Complements:

- [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) — source-tree tests on PR/push
- Publish workflows — `publish-crates.yml`, `publish-pypi-sdk.yml`, `publish-npm-sdk.yml`, `publish-pypi.yml`

## Local run (after a release is on registries)

```bash
export CYT_RELEASE_VERSION=0.1.10   # must match a published version
./sdk/e2e/scripts/render-manifests.sh
./sdk/e2e/scripts/run-all.sh
```

Or run one target:

```bash
export CYT_RELEASE_VERSION=0.1.10
./sdk/e2e/scripts/render-manifests.sh
./sdk/e2e/scripts/wait-registry.sh pypi-sdk
cd sdk/e2e/python && uv sync --group test && uv run pytest
```

`wait-registry.sh` targets: `crate`, `pypi-sdk`, `pypi-app`, `npm`.

## Manifest templates

Version pins live in `*.in` templates (`@CYT_RELEASE_VERSION@` placeholder). `render-manifests.sh` writes gitignored
`Cargo.toml`, `pyproject.toml`, and `package.json` files so PRs do not churn release versions.

## Scripts

| Script | Purpose |
| ------ | ------- |
| [`scripts/parse-version.sh`](scripts/parse-version.sh) | Parse semver from `TAG` or `CYT_RELEASE_VERSION` |
| [`scripts/render-manifests.sh`](scripts/render-manifests.sh) | Generate manifests from `.in` templates |
| [`scripts/wait-registry.sh`](scripts/wait-registry.sh) | Poll registry until version is installable |
| [`scripts/run-all.sh`](scripts/run-all.sh) | Full local smoke (all four targets) |
