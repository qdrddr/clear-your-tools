#!/usr/bin/env bash
# Simulate Cursor beforeSubmitPrompt via local cyt-client + hook daemon.
#
# Uses repo source (uv run), NOT the globally installed cyt/cyt-client binaries.
# Your earlier one-liner failed in zsh because unquoted JSON triggers globbing on
# characters like [ and { (e.g. attachments:[type:rule]).
#
# Usage:
#   bash scripts/mcpc/simulate-cursor-hook.sh [session_id] [prompt] [runs] [--fresh]
#
# Examples:
#   bash scripts/mcpc/simulate-cursor-hook.sh
#   bash scripts/mcpc/simulate-cursor-hook.sh dbd9f486-d3c9-44aa-a35a-2571841113d9 "BM25 pipeline?" 3 --fresh
set -euo pipefail

REPO="/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/qdrddr/clear-your-tools"
SESSION_ID="${1:-dbd9f486-d3c9-44aa-a35a-2571841113d9}"
PROMPT="${2:-Where is my primary BM25 pruning pipeline located in codebase?}"
RUNS="${3:-2}"
FRESH=false
for arg in "$@"; do
	if [[ "$arg" == "--fresh" ]]; then
		FRESH=true
	fi
done

cd "$REPO"

echo "==> Restarting hook daemon from repo source"
uv run src/cyt/proxy/cli.py hook daemon restart

echo "==> Waiting for daemon"
for _ in $(seq 1 30); do
	if curl -sf "http://127.0.0.1:8834/health" >/dev/null 2>&1; then
		break
	fi
	sleep 0.5
done

SESSION_LOG="$REPO/.cursor/cyt/sessions/${SESSION_ID}.jsonl"

echo "==> Session log: $SESSION_LOG"
mkdir -p "$(dirname "$SESSION_LOG")"
if [[ "$FRESH" == true ]]; then
	printf '{"type":"meta","agent":"cursor"}\n' >"$SESSION_LOG"
	echo "    (cleared previous entries; kept meta line)"
fi

run_hook() {
	local run_prompt="$1"
	local generation_id
	generation_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
	echo
	echo "---- hook run: $run_prompt ----"
	CYT_LAUNCH_AGENT=cursor uv run src/cyt_client/cli.py <<EOF
{
  "conversation_id": "$SESSION_ID",
  "generation_id": "$generation_id",
  "model": "composer-2.5-fast",
  "model_id": "composer-2.5",
  "composer_mode": "agent",
  "prompt": "$run_prompt",
  "attachments": [
    {
      "type": "rule",
      "file_path": "cyt-injection.mdc"
    }
  ],
  "session_id": "$SESSION_ID",
  "hook_event_name": "beforeSubmitPrompt",
  "cursor_version": "3.12.17",
  "workspace_roots": [
    "$REPO"
  ],
  "transcript_path": "/Users/dberezenko/.cursor/projects/Volumes-OWCExpress1M2-Users-dberezenko-git-github-com-qdrddr-clear-your-tools/agent-transcripts/${SESSION_ID}/${SESSION_ID}.jsonl"
}
EOF
}

for i in $(seq 1 "$RUNS"); do
	run_hook "$PROMPT (run $i)"
done

echo
echo "==> Hashes in session log (key -> unique hashes)"
if [[ -f "$SESSION_LOG" ]]; then
	uv run python - "$SESSION_LOG" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

path = Path(sys.argv[1])
by_key: dict[str, set[str]] = defaultdict(set)
full_flags: dict[str, set[bool]] = defaultdict(set)
for line in path.read_text(encoding="utf-8").splitlines():
    if not line.strip() or line.startswith('{"type":"meta"'):
        continue
    row = json.loads(line)
    key = str(row.get("key") or "")
    h = str(row.get("hash") or "")
    if key and h:
        by_key[key].add(h)
        full_flags[key].add(bool(row.get("full")))

for key in sorted(by_key):
    hashes = sorted(by_key[key])
    flags = sorted(full_flags[key])
    status = "OK" if len(hashes) == 1 else "UNSTABLE"
    print(f"{status} {key}")
    print(f"  full flags: {flags}")
    for h in hashes:
        print(f"  hash: {h}")
PY
else
	echo "No session log written."
fi

echo
echo "Done. Inspect full log with:"
echo "  cat '$SESSION_LOG'"
