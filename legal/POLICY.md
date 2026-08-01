# License policy

This document states how **clear-your-tools** handles first-party and third-party
licenses. It is the policy reference for FOSSA, `deny.toml`, and
`scripts/legal/*` audits.

## First-party

All first-party source, SDKs, and published packages in this repository are
**Apache-2.0** (see root `LICENSE` and per-package `LICENSE` / `NOTICE` files).

## Distributed third-party

Dependencies that ship to end users (PyPI wheels/sdists, optional extras, native
extensions, and runtime Rust/npm/go deps in release artifacts) must use
**permissive licenses only**.

Allowed SPDX identifiers for distributed dependencies:

- MIT
- Apache-2.0
- Apache-2.0 WITH LLVM-exception
- BSD-2-Clause
- BSD-3-Clause
- 0BSD
- ISC
- Unicode-3.0
- Unlicense
- Zlib

**Not allowed** in the distributed graph: GPL, LGPL, AGPL, and other strong or
weak copyleft licenses (including MPL-2.0) unless explicitly approved for a
specific release after legal review.

## Dev / CI third-party

Development and CI-only dependencies (for example linters, test runners, and
release tooling) may include **LGPL** and other copyleft licenses when they are
**not redistributed** with the product.

Dev/CI dependencies must not appear in `requirements.txt` or release SBOMs. They
belong in `requirements-dev.txt` only.

## FOSSA release gate

FOSSA compliance for releases and the public badge is evaluated against
**production / distributed dependencies only** (see `.fossa.yml` and `fossa-deps.yml`).

- `fossa-deps.yml` — Python runtime graph (from `requirements.txt`) plus Rust SDK
  binding crates for `cyt-indexer` (`python`/`node` features; FOSSA `cargo@.` uses
  default `cli` only — see [FOSSA Rust docs](https://docs.fossa.com/docs/project-setup/supported-languages/rust))
- `requirements.txt` — runtime / distributed deps (all published optional extras)
- `requirements-dev.txt` — dev + test groups (excluded via `.fossa.yml`)
- `uv.lock` — developer lockfile (excluded from FOSSA; source for exports)
- `cargo@.` — Rust runtime graph for `cyt-indexer` default features (`cli`);
  SDK binding crates (`pyo3`, `napi`, …) are listed in `fossa-deps.yml` instead.
  Build/dev crates such as `cbindgen`, `cucumber`, and `tokio` are not shipped.
- `npm@sdk/typescript` — published TypeScript SDK (root `npm@.` is excluded; CI-only)
- `gomod@sdk/go` — Go SDK runtime module (no third-party runtime deps)

Regenerate committed requirements and FOSSA referenced-deps after lockfile changes:

```bash
./scripts/deps/export-requirements.sh
./scripts/local/dev/cargo-build-sdk-release.sh   # cyt-indexer python+node release build
```

Local verification:

- **Release gate:** `./scripts/deps/export-requirements.sh --check` or
  `./scripts/legal/audit-all.sh` (default; production deps)
- **Full dev inventory:** `./scripts/legal/audit-all.sh --with-dev`

Rust release dependencies are checked with `cargo deny` per root `deny.toml`.
Copyleft licenses must not be added to `deny.toml` globally; use crate-specific
exceptions only for documented dev-only Rust crates, if ever needed.
