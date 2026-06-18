#!/usr/bin/env bash
# Run fallow checks the same way as: cd ui && task all-fallow
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${ROOT}/node_modules/.bin:${PATH}"

cd "${ROOT}/ui"
task all-fallow
