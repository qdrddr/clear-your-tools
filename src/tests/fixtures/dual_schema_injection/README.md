# Dual-schema tier injection fixtures

Fixtures for `cyt_backend_input_schema` + tier-scoped `input_schema` stamping and backend-aware injection hints.

## Layout

- `tools.json` — synthetic catalog tools covering backend profiles:
  - `dual_tool_empty` — parameterless (empty `properties`)
  - `dual_tool_required_only` — required properties only
  - `dual_tool_required_optional` — required + optional
  - `dual_tool_optional_only` — optional-only (T2 hint profile)
  - `dual_tool_purge` — required `confirm` (T2 merge invariant)
- `scenarios.json` — parametrized cases for unit and integration tests:
  - `stamp_cases` — tier prep dual-schema stamping
  - `hint_cases` — `injection_needs_definitions_lookup` + `format_tool_item` output
  - `ensure_merge_cases` — `ensure_tool_injection_schema` required merge
  - `integration` — live `filter_tools_for_query` end-to-end checks (T2 hint, T3 empty schema, T2 required merge, T4 direct)
- `injection_table.json` — canonical **33-row** matrix for plan section 4 (*Backend-aware injection hint*):
  - format outcomes (`description_only`, `hint`, `explicit_empty_schema`, schema variants)
  - `absent_dedup` — pre-exposure dedup skips re-injection
  - `absent_excluded` — T0 excluded from BM25 pool
  - `absent_prune` — tool dropped before injection reaches formatting

## Tests

- Unit: `src/tests/unit/test_dual_schema_injection.py`
- Unit (section 4 table): `src/tests/unit/test_dual_schema_injection_table.py`
- Integration: `src/tests/integration/test_dual_schema_injection_integration.py`
- Loader: `src/tests/support/dual_schema_injection_fixtures.py`
