# TypeScript SDK parity tests

Cross-language checks that TypeScript N-API bindings match the Python `cyt_indexer._native` reference.

Requires repo-root `uv sync` so `uv run python -c "import cyt_indexer"` succeeds. Skip with `CYT_SKIP_PARITY=1`.

Unit tests (smoke and pure TypeScript helpers) live in [`../unit/`](../unit/).
