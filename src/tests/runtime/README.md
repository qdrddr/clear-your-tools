# Runtime integration tests

Manual-only tests that may start real **cyt hook daemon** or **cyt launch proxy**
processes on localhost.

These never run from pre-commit hooks, `workflow.sh all`, or
`prek-loop.sh` unless you opt in explicitly.

## Run

```bash
./scripts/local/tests/pytest-category.sh runtime
./scripts/local/dev/workflow.sh app-test-runtime
./scripts/local/dev/workflow.sh --runtime all
./scripts/pre-commit-hooks/prek-loop.sh --runtime --one-run -g runtime
```

Or directly:

```bash
CYT_RUN_RUNTIME_TESTS=1 uv run pytest -m runtime --run-runtime src/tests/runtime
```

Mark new tests with `@pytest.mark.runtime` and place them under this directory.
