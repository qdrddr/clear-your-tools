#!/usr/bin/env bash
# Merge examples/agents/cursor/hooks.json into ~/.cursor/hooks.json.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_JSON="${HOME}/.cursor/hooks.json"

die() {
	echo "error: $*" >&2
	exit 1
}

command -v jq >/dev/null 2>&1 || die "jq is required (macOS: brew install jq; Debian/Ubuntu: apt install jq)"

[[ -f "${SCRIPT_DIR}/hooks.json" ]] || die "missing overlay: ${SCRIPT_DIR}/hooks.json"

JQ_DEEPMERGE="$(
	cat <<'EOF'
def deepmerge(a; b):
  if (a | type) == "object" and (b | type) == "object" then
    (a | to_entries) + (b | to_entries)
    | group_by(.key)
    | map({
        key: .[0].key,
        value: (
          if length == 1 then .[0].value
          elif (.[0].value | type) == "object" and (.[1].value | type) == "object"
          then deepmerge(.[0].value; .[1].value)
          else .[1].value
          end
        )
      })
    | from_entries
  else
    b
  end;
deepmerge(.[0]; $overlay)
EOF
)"

tmp="$(mktemp "${TMPDIR:-/tmp}/cursor-hooks.XXXXXX.json")"
trap 'rm -f "${tmp}"' EXIT

if [[ -f "${TARGET_JSON}" ]]; then
	jq -s --slurpfile overlay "${SCRIPT_DIR}/hooks.json" "${JQ_DEEPMERGE}" "${TARGET_JSON}" >"${tmp}"
else
	cp "${SCRIPT_DIR}/hooks.json" "${tmp}"
fi

mv "${tmp}" "${TARGET_JSON}"
trap - EXIT

echo "merged ${SCRIPT_DIR}/hooks.json -> ${TARGET_JSON}"
echo "Restart Cursor or reload hooks if they do not pick up immediately."
