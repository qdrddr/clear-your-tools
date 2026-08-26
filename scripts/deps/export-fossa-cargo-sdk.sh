#!/usr/bin/env bash
# Emit FOSSA referenced-dependencies YAML for cyt-indexer SDK bindings (python+node).
#
# FOSSA cargo@. uses default features (cli) and misses optional binding crates.
# This script diffs cargo metadata for --features python,node vs the default graph
# and lists registry crates present only in the SDK build.
#
# Usage:
#   ./scripts/deps/export-fossa-cargo-sdk.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck source=scripts/lib/chunk-worktree.sh
source "${REPO_ROOT}/scripts/lib/chunk-worktree.sh"

while [[ $# -gt 0 ]]; do
	case "$1" in
	-h | --help)
		cat <<'EOF'
Usage: export-fossa-cargo-sdk.sh

Prints referenced-dependencies YAML entries (type: cargo) for cyt-indexer SDK
binding crates missing from FOSSA's default cargo@. scan.
EOF
		exit 0
		;;
	*)
		echo "error: unknown arg: $1 (try --help)" >&2
		exit 1
		;;
	esac
done

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "error: missing required command: $1" >&2
		exit 1
	}
}

resolve_python() {
	local candidate
	for candidate in python3 python; do
		if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "import sys" >/dev/null 2>&1; then
			echo "${candidate}"
			return 0
		fi
	done
	if [[ -x "${REPO_ROOT}/.venv/Scripts/python.exe" ]]; then
		echo "${REPO_ROOT}/.venv/Scripts/python.exe"
		return 0
	fi
	if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
		echo "${REPO_ROOT}/.venv/bin/python"
		return 0
	fi
	echo "error: missing python (install Python or run: uv sync)" >&2
	exit 1
}

metadata_to_temp() {
	local -a extra=()
	if (($#)); then
		extra=("$@")
	fi
	local tmp
	tmp="$(mktemp "${TMPDIR:-/tmp}/cyt-cargo-metadata-XXXXXX")"
	if ! chunk_cargo_locked "${REPO_ROOT}" metadata --locked --format-version 1 --quiet "${extra[@]}" >"${tmp}" 2>/dev/null; then
		rm -f "${tmp}"
		echo "error: cargo metadata failed (${extra[*]:-default graph})" >&2
		return 1
	fi
	echo "${tmp}"
}

emit_cargo_entries() {
	local python_bin
	python_bin="$(resolve_python)"
	local default_meta sdk_meta
	default_meta="$(metadata_to_temp)"
	sdk_meta="$(metadata_to_temp --manifest-path sdk/rust/cyt-indexer/Cargo.toml --no-default-features --features python,node)"
	trap 'rm -f "${default_meta}" "${sdk_meta}"' RETURN

	"${python_bin}" - "${default_meta}" "${sdk_meta}" <<'PY'
import json
import sys


def registry_keys(path: str) -> dict[tuple[str, str], dict]:
    with open(path, encoding="utf-8") as handle:
        meta = json.load(handle)
    keys: dict[tuple[str, str], dict] = {}
    for package in meta.get("packages", []):
        source = package.get("source") or ""
        if not source.startswith("registry+"):
            continue
        keys[(package["name"], package["version"])] = package
    return keys


default_keys = registry_keys(sys.argv[1])
sdk_keys = registry_keys(sys.argv[2])
added = sorted(sdk_keys.keys() - default_keys.keys(), key=lambda item: item[0].lower())
for name, version in added:
    print(f'  - type: cargo\n    name: {name}\n    version: "{version}"')
PY
}

emit_cargo_entries
