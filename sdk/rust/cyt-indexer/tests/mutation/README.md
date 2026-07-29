# Mutation testing (Rust)

Precise regression tests for [cargo-mutants](https://mutants.rs/) (`mutants.toml` when enabled).

```bash
cargo mutants -p cyt-indexer
./scripts/local/tests/cargo-test-category.sh mutation
```
