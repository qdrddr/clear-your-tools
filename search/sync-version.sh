#!/usr/bin/env bash
# Update pyproject.toml first!
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYPROJECT="${ROOT}/pyproject.toml"
PUBLISH="${ROOT}/search/publish.sh"

version="$(
  grep -E '^version[[:space:]]*=' "${PYPROJECT}" \
    | head -1 \
    | sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
)"

if [[ -z "${version}" ]]; then
  echo "error: could not read version from ${PYPROJECT}" >&2
  exit 1
fi

tag="v${version}"
if [[ "$(uname -s)" == Darwin ]]; then
  sed -i '' "s/^tag=.*/tag=${tag}/" "${PUBLISH}"
else
  sed -i "s/^tag=.*/tag=${tag}/" "${PUBLISH}"
fi

echo "set ${PUBLISH} tag=${tag} (from pyproject version ${version})"
