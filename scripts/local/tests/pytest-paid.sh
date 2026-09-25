#!/usr/bin/env bash
# Run paid LLM/reranking integration tests (manual only — never pre-commit/prek-loop).
#
# Usage:
#   ./scripts/local/tests/pytest-paid.sh [pytest args...]
#
# Requires configured pruning LLM credentials (e.g. OPENROUTER_API_KEY).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${ROOT}"

exec env CYT_RUN_INTEGRATION_TESTS=1 CYT_RUN_PAID_TESTS=1 uv run pytest \
	src/tests/integration/test_llm_prune_integration.py \
	src/tests/integration/gherkin/test_llm_prune_gherkin.py \
	-m paid \
	--run-integration \
	--run-paid \
	"$@"
