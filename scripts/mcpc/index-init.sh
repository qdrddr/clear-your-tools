#!/usr/bin/env bash
# One-time bootstrap of code indexes via MCPC tools + optional CLI fallbacks.
#
# Run ./scripts/mcpc/index-install.sh first to install CLI/MCP tools.
# MCPC manifest covers: jcodemunch, codebase-memory, code-review-graph.
# CLI fallbacks follow ~/.cursor/skills/local-search/SKILL.md (Install + Indexing):
#   codebase-memory-mcp, jcodemunch-mcp, colgrep, codegraph, graphify, gitnexus.
#
# Usage:
#   ./scripts/mcpc/index-init.sh
#   ./scripts/mcpc/index-init.sh --dry-run
#   ./scripts/mcpc/index-init.sh --mcpc-only
#
# Regenerate payloads first:
#   ./scripts/mcpc/generate-tool-payloads.sh
#
# Post-commit incremental updates:
#   ./scripts/mcpc/index-update.sh --post-commit

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/mcpc-run.sh"

MANIFEST="${SCRIPT_DIR}/manifests/index-init.tsv"
PAYLOADS_DIR="${PAYLOADS_DIR:-${SCRIPT_DIR}/payloads/index-init}"
DRY_RUN=0
MCPC_ONLY=0
CLI_FAIL=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--mcpc-only)
		MCPC_ONLY=1
		shift
		;;
	*)
		echo "error: unknown argument: $1" >&2
		exit 1
		;;
	esac
done

mcpc_run_require_tools

if [[ ! -f "$MANIFEST" ]]; then
	echo "error: manifest not found: $MANIFEST" >&2
	exit 1
fi
if [[ ! -d "$PAYLOADS_DIR" ]]; then
	echo "error: payloads dir not found: $PAYLOADS_DIR" >&2
	echo "hint: run ./scripts/mcpc/generate-tool-payloads.sh" >&2
	exit 1
fi

resolve_index_context "$SCRIPT_DIR/../.."
cd "$REPO_ROOT"

OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/output/index-init-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"
SUMMARY_FILE="${OUTPUT_DIR}/summary.tsv"
printf 'session\ttool\texit_text\texit_json\tout\tjson\n' >"$SUMMARY_FILE"

echo "Repo root: $REPO_ROOT"
echo "jcodemunch repo: ${JM_REPO:-<unknown>}"
echo "codebase-memory project: ${CB_PROJECT:-<unknown>}"
echo "gitnexus repo: ${GITNEXUS_REPO:-clear-your-tools}"
echo

run_manifest "$MANIFEST" "$PAYLOADS_DIR" "$DRY_RUN"

echo "MCPC done: pass=$MANIFEST_PASS fail=$MANIFEST_FAIL skip=$MANIFEST_SKIP"

if [[ "$MCPC_ONLY" -eq 1 || "$DRY_RUN" -eq 1 ]]; then
	echo "Output: $OUTPUT_DIR"
	echo "Summary: $SUMMARY_FILE"
	[[ "$MANIFEST_FAIL" -eq 0 ]]
	exit $?
fi

if [[ "$MANIFEST_FAIL" -ne 0 ]]; then
	echo "error: MCPC init failed" >&2
	exit 1
fi

echo
run_local_search_init_cli || CLI_FAIL=$?

echo
echo "Done: mcpc pass=$MANIFEST_PASS fail=$MANIFEST_FAIL skip=$MANIFEST_SKIP cli_fail=$CLI_FAIL"
echo "Output: $OUTPUT_DIR"
echo "Summary: $SUMMARY_FILE"
[[ "$CLI_FAIL" -eq 0 ]]
