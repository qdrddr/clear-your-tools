# qa (Python)

Manual harness tests (not default CI). BM25 smoke against shared e2e fixtures.

```bash
./scripts/local/tests/pytest-category.sh qa
uv run src/tests/qa/bm25_scratch.py "read files from disk"
uv run src/tests/qa/tool_examples_query.py 04_oauth_gitnexus
uv run pytest src/tests/qa/test_tool_examples_bm25_query.py -m qa --run-qa \
  --tool-examples-query-id=04_oauth_gitnexus -s
```
