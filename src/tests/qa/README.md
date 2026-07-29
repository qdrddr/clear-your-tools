# qa (Python)

Manual harness tests (not default CI). BM25 smoke against shared e2e fixtures.

```bash
./scripts/pytest-category.sh qa
uv run src/tests/qa/bm25_scratch.py "read files from disk"
```
