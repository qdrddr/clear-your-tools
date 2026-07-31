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
**`requirements.txt` (production)** only.

- `requirements.txt` — runtime / distributed deps (all published optional extras)
- `requirements-dev.txt` — dev + test groups (excluded via `.fossa.yml`)
- `uv.lock` — developer lockfile (excluded from FOSSA; source for exports)

Regenerate committed requirements after lockfile changes:

```bash
./scripts/deps/export-requirements.sh
```

Local verification:

- **Release gate:** `./scripts/deps/export-requirements.sh --check` or
  `./scripts/legal/audit-all.sh` (default; production deps)
- **Full dev inventory:** `./scripts/legal/audit-all.sh --with-dev`

Rust release dependencies are checked with `cargo deny` per root `deny.toml`.
Copyleft licenses must not be added to `deny.toml` globally; use crate-specific
exceptions only for documented dev-only Rust crates, if ever needed.
