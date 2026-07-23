#!/usr/bin/env bash
# One-time install of local-search / MCP CLI tools and MCPC sessions.
#
# Installs (when available on PATH):
#   codebase-memory-mcp, hedl-mcp, semble, code-review-graph, context-mode,
#   lean-ctx, colgrep, executor, codegraph, graphify, fff-mcp, jcodemunch-mcp,
#   gitnexus, rtk, MCPC connects (context7, deepwiki, coolgrep-skill, stdio).
#
# Does NOT index repos — run index-init.sh afterward.
#
# Usage:
#   ./scripts/mcpc/index-install.sh
#   ./scripts/mcpc/index-install.sh --dry-run
#   ./scripts/mcpc/index-install.sh --skip-mcpc
#   ./scripts/mcpc/index-install.sh --with-dbhub

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/mcpc-run.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/lib/index-install.sh"

INSTALL_DRY_RUN=0
INSTALL_SKIP_MCPC=0
INSTALL_WITH_DBHUB=0

while [[ $# -gt 0 ]]; do
	case "$1" in
	--dry-run)
		INSTALL_DRY_RUN=1
		shift
		;;
	--skip-mcpc)
		INSTALL_SKIP_MCPC=1
		shift
		;;
	--with-dbhub)
		INSTALL_WITH_DBHUB=1
		shift
		;;
	*)
		echo "error: unknown argument: $1" >&2
		exit 1
		;;
	esac
done

export INSTALL_DRY_RUN INSTALL_SKIP_MCPC INSTALL_WITH_DBHUB

resolve_repo_root "$SCRIPT_DIR/../.."
cd "$REPO_ROOT"

OUTPUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/output/index-install-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$OUTPUT_DIR"
INSTALL_LOG="${OUTPUT_DIR}/install.log"

{
	run_local_search_install
} 2>&1 | tee "$INSTALL_LOG"

exit "${PIPESTATUS[0]}"
