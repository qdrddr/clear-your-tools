#!/usr/bin/env bash
# Run all automated app pytest categories (mirrors prek hooks; excludes manual qa).
#
# Usage:
#   ./scripts/pytest-app-ci.sh [extra pytest args forwarded to each category]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

categories=(unit gherkin-unit quality_metrics coverage mutation)
for category in "${categories[@]}"; do
	bash "${SCRIPT_DIR}/pytest-category.sh" "${category}" "$@"
done
