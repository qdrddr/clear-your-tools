#!/usr/bin/env bash
# Update pyproject.toml first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="${ROOT}/pyproject.toml"
PUBLISH="${ROOT}/search/publish.sh"

read_version() {
  grep -E '^version[[:space:]]*=' "${PYPROJECT}" \
    | head -1 \
    | sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
}

version="$(read_version)"

if [[ -z "${version}" ]]; then
  echo "error: could not read version from ${PYPROJECT}" >&2
  exit 1
fi

tag="v${version}"

# publish.sh reads pyproject.toml directly; keep an untracked sidecar so prek
# does not fail with "files were modified by this hook" on version bumps.
TAG_FILE="${ROOT}/search/.publish-tag"
if [[ -f "${TAG_FILE}" ]] && grep -qxF "tag=${tag}" "${TAG_FILE}"; then
  echo "${PUBLISH} tag=${tag} already synced (pyproject version ${version})"
  exit 0
fi

printf 'tag=%s\n' "${tag}" > "${TAG_FILE}"
echo "set ${PUBLISH} tag=${tag} (from pyproject version ${version})"
exit 0
