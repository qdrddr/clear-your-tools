# qa (Rust cyt-indexer)

Manual harness scripts (not CI). BM25 smoke against shared e2e fixtures.

```bash
cargo test -p cyt-indexer --features testing --test qa_bm25_scratch -- --nocapture -- "read files from disk"
./scripts/cargo-test-category.sh qa
```
