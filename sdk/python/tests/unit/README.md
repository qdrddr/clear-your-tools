# Python SDK unit tests

Binding and wrapper tests for `cyt_indexer` Python APIs. Algorithm and FFI depth is covered in
[`../../rust/cyt-indexer/tests/`](../../rust/cyt-indexer/tests/).

Run from repo root after syncing the editable SDK:

```bash
cd sdk/python
uv run maturin develop --release
uv run pytest tests/unit
```
