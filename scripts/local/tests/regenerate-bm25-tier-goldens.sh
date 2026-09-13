#!/usr/bin/env bash
# Regenerate tier-scoped BM25 prune golden files under
# src/tests/fixtures/cyt_mcp_catalog_tiered/out/
#
# Run after BM25 weight changes, tier adapter updates, or merge_t4_tools behavior changes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
CYT_REGENERATE_BM25_TIER_GOLDENS=1 uv run pytest \
  src/tests/unit/test_cyt_mcp_catalog_bm25_prune_tiered.py -q
