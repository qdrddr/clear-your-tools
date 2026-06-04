#!/usr/bin/env bash
# Deep-merge examples/agents/anthropic/claude/settings.json into ~/.claude/settings.json (Desktop).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_JSON="${SCRIPT_DIR}/settings.json"
TARGET_JSON="${HOME}/.claude/settings.json"

die() {
	echo "error: $*" >&2
	exit 1
}

command -v jq >/dev/null 2>&1 || die "jq is required (macOS: brew install jq; Debian/Ubuntu: apt install jq)"

[[ -f "${OVERLAY_JSON}" ]] || die "missing overlay: ${OVERLAY_JSON}"

read_overlay() {
	sed -E '/^[[:space:]]*\/\//d; /^[[:space:]]*$/d' "${OVERLAY_JSON}"
}

OVERLAY_PARSED="$(read_overlay)"
echo "${OVERLAY_PARSED}" | jq -e . >/dev/null || die "invalid JSON in ${OVERLAY_JSON}"
OVERLAY_COMPACT="$(echo "${OVERLAY_PARSED}" | jq -c .)"

mkdir -p "$(dirname "${TARGET_JSON}")"

JQ_DEEPMERGE="$(cat <<'EOF'
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

tmp="$(mktemp "${TMPDIR:-/tmp}/claude-settings.XXXXXX.json")"
trap 'rm -f "${tmp}"' EXIT

if [[ -f "${TARGET_JSON}" ]]; then
	jq -s --argjson overlay "${OVERLAY_COMPACT}" "${JQ_DEEPMERGE}" "${TARGET_JSON}" >"${tmp}"
else
	echo "${OVERLAY_PARSED}" | jq . >"${tmp}"
fi

mv "${tmp}" "${TARGET_JSON}"
trap - EXIT

echo "merged ${OVERLAY_JSON} -> ${TARGET_JSON}"
