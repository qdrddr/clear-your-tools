#!/usr/bin/env bash
# Incremental index refresh after commits via MCPC tools + optional CLI fallbacks.
#
# MCPC manifest covers: jcodemunch, codebase-memory, code-review-graph, gitnexus.
# CLI fallbacks follow ~/.cursor/skills/local-search/SKILL.md (Indexing):
#   jcodemunch-mcp upgrade/index, gitnexus detect-changes, codegraph sync, graphify update.
#
# Usage:
#   ./scripts/mcpc/index-update.sh
#   ./scripts/mcpc/index-update.sh --post-commit
#   ./scripts/mcpc/index-update.sh --base main
#   ./scripts/mcpc/index-update.sh --dry-run
#   ./scripts/mcpc/index-update.sh --skip-cli
#
# Post-commit hook example:
#   REPO_ROOT="$(git rev-parse --show-toplevel)"
#   "$REPO_ROOT/scripts/mcpc/index-update.sh" --post-commit \
#     >>"$REPO_ROOT/.git/index-update.log" 2>&1 &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/mcpc-run.sh"

MANIFEST="${SCRIPT_DIR}/manifests/index-update.tsv"
PAYLOADS_DIR="${PAYLOADS_DIR:-${SCRIPT_DIR}/payloads/index-update}"
DRY_RUN=0
SKIP_CLI=0
BASE_REF="HEAD~1"
CLI_FAIL=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		DRY_RUN=1
		shift
		;;
	--post-commit)
		BASE_REF="HEAD~1"
		shift
		;;
	--base)
		BASE_REF="${2:-HEAD~1}"
		shift 2
		;;
	--skip-cli)
		SKIP_CLI=1
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

mapfile -t CHANGED_FILES < <(git diff --name-only --diff-filter=ACMR "${BASE_REF}..HEAD" 2>/dev/null || true)
CHANGED_FILES_JSON="$(printf '%s\n' "${CHANGED_FILES[@]}" | jq -R -s 'split("\n") | map(select(length > 0))')"
export CHANGED_FILES_JSON

OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/output/index-update-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"
SUMMARY_FILE="${OUTPUT_DIR}/summary.tsv"
printf 'session\ttool\texit_text\texit_json\tout\tjson\n' >"$SUMMARY_FILE"

echo "Repo root: $REPO_ROOT"
echo "Base ref: $BASE_REF"
echo "Changed files: $(jq 'length' <<<"$CHANGED_FILES_JSON")"
echo "jcodemunch repo: ${JM_REPO:-<unknown>}"
echo "codebase-memory project: ${CB_PROJECT:-<unknown>}"
echo "gitnexus repo: ${GITNEXUS_REPO:-clear-your-tools}"
echo

run_manifest "$MANIFEST" "$PAYLOADS_DIR" "$DRY_RUN"

echo "MCPC done: pass=$MANIFEST_PASS fail=$MANIFEST_FAIL skip=$MANIFEST_SKIP"

if [[ "$SKIP_CLI" -eq 1 || "$DRY_RUN" -eq 1 ]]; then
	echo "Output: $OUTPUT_DIR"
	echo "Summary: $SUMMARY_FILE"
	[[ "$MANIFEST_FAIL" -eq 0 ]]
	exit $?
fi

if [[ "$MANIFEST_FAIL" -ne 0 ]]; then
	echo "error: MCPC update failed" >&2
	exit 1
fi

echo
run_local_search_update_cli || CLI_FAIL=$?

echo
echo "Done: mcpc pass=$MANIFEST_PASS fail=$MANIFEST_FAIL skip=$MANIFEST_SKIP cli_fail=$CLI_FAIL"
echo "Output: $OUTPUT_DIR"
echo "Summary: $SUMMARY_FILE"
[[ "$CLI_FAIL" -eq 0 ]]
