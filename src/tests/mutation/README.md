# Mutation testing (Python)

Precise regression tests for [mutmut](https://mutmut.readthedocs.io/) on pure Python modules
(`cyt.pruners.catalog_common`, `cyt.pruners.bm25`).

```bash
./scripts/pytest-category.sh mutation
# Example (after configuring mutmut):
# mutmut run --paths-to-mutate src/cyt/pruners/catalog_common.py,src/cyt/pruners/bm25.py
```
