# Runtime integration tests

Manual-only tests that start real **cyt hook daemon** or **cyt launch proxy**
processes on localhost (subprocess + HTTP).

These never run from pre-commit hooks, `workflow.sh all`, or
`prek-loop.sh` unless you opt in explicitly.

## Scope

| Module | What it covers |
|--------|----------------|
| [`test_runtime_opt_in.py`](test_runtime_opt_in.py) | Category smoke |
| [`test_hook_daemon_e2e.py`](test_hook_daemon_e2e.py) | Real hook daemon: `/health`, tools BM25 inject, skills frontmatter gate, live tiers |
| [`test_launch_proxy_e2e.py`](test_launch_proxy_e2e.py) | Real launch proxy + mock upstream; asserts pruned tools forwarded |

Shared helpers: [`../support/runtime_e2e_fixtures.py`](../support/runtime_e2e_fixtures.py)

Unit/integration tests under `src/tests/unit/` and `src/tests/integration/` use in-process
ASGI or `run_hook_payload` and do **not** spawn subprocesses (unless marked `@pytest.mark.runtime`).

## Run

```bash
./scripts/local/tests/pytest-category.sh runtime
./scripts/local/dev/workflow.sh app-test-runtime
./scripts/local/dev/workflow.sh --runtime all
./scripts/local/dev/prek-loop.sh --runtime --one-run -g runtime
```

Or directly:

```bash
CYT_RUN_RUNTIME_TESTS=1 uv run pytest -m runtime --run-runtime src/tests/runtime
```

## Notes

- Each test binds an ephemeral port; startup waits up to `STARTUP_TIMEOUT_SECONDS` (35s) from `cyt.hook.daemon`.
- Set `HOME` to a temp directory so BM25 indexes and tier DBs stay isolated.
- Mark new tests with `@pytest.mark.runtime` and place them under this directory.
