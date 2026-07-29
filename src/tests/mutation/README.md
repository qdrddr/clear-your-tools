# Mutation testing (Python)

Manual runs with [mutmut](https://mutmut.readthedocs.io/). Start with pure modules in `cyt_core` and `cyt.pruners.bm25`.

```bash
task test-mutation   # prints this scaffold + Rust counterpart
# Example (after configuring mutmut):
# mutmut run --paths-to-mutate src/cyt/pruners/bm25.py
```
