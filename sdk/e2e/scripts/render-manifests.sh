#!/usr/bin/env bash
# Render gitignored manifests from .in templates using CYT_RELEASE_VERSION.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${CYT_RELEASE_VERSION:-}"

if [[ -z "$VERSION" ]]; then
	if [[ -n "${TAG:-}" ]]; then
		# shellcheck source=parse-version.sh
		eval "$("${ROOT}/scripts/parse-version.sh")"
	else
		echo "CYT_RELEASE_VERSION or TAG must be set" >&2
		exit 1
	fi
fi

render() {
	local src="$1"
	local dst="$2"
	sed "s/@CYT_RELEASE_VERSION@/${VERSION}/g" "$src" >"$dst"
	echo "rendered ${dst}"
}

render_rust_cargo() {
	local dst="${ROOT}/rust/Cargo.toml"
	if [[ "${CYT_E2E_USE_WORKSPACE:-}" == "1" ]]; then
		cat >"$dst" <<'EOF'
[workspace]

[package]
name = "cyt-indexer-registry-e2e"
version = "0.0.0"
edition = "2021"
publish = false

[dependencies]
cyt-indexer = { path = "../../rust/cyt-indexer" }
serde_json = "1"
EOF
		echo "rendered ${dst} (workspace path=../../rust/cyt-indexer)"
		return 0
	fi
	render "${ROOT}/rust/Cargo.toml.in" "$dst"
}

render_python_pyproject() {
	local dst="${ROOT}/python/pyproject.toml"
	if [[ "${CYT_E2E_USE_WORKSPACE:-}" == "1" ]]; then
		cat >"$dst" <<'EOF'
[project]
name = "cyt-indexer-sdk-registry-e2e"
version = "0.0.0"
requires-python = ">=3.13,<4.0"
dependencies = ["cyt-indexer-sdk"]

[dependency-groups]
test = ["pytest>=8.0"]

[tool.uv.sources]
cyt-indexer-sdk = { path = "../../python", editable = true }

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF
		echo "rendered ${dst} (workspace path=../../python)"
		return 0
	fi
	render "${ROOT}/python/pyproject.toml.in" "$dst"
}

render_typescript_package() {
	local dst="${ROOT}/typescript/package.json"
	if [[ "${CYT_E2E_USE_WORKSPACE:-}" == "1" ]]; then
		cat >"$dst" <<'EOF'
{
  "name": "cyt-indexer-sdk-registry-e2e",
  "private": true,
  "type": "module",
  "scripts": {
    "test": "node test/run.mjs"
  },
  "devDependencies": {
    "cyt-indexer-sdk": "file:../../typescript"
  }
}
EOF
		echo "rendered ${dst} (workspace file:../../typescript)"
		return 0
	fi
	render "${ROOT}/typescript/package.json.in" "$dst"
}

render_clear_your_tools_pyproject() {
	local dst="${ROOT}/clear-your-tools/pyproject.toml"
	if [[ "${CYT_E2E_USE_WORKSPACE:-}" == "1" ]]; then
		cat >"$dst" <<'EOF'
[project]
name = "clear-your-tools-registry-e2e"
version = "0.0.0"
requires-python = ">=3.13,<4.0"
dependencies = ["clear-your-tools[all]"]

[dependency-groups]
test = ["pytest>=8.0"]

[tool.uv.sources]
clear-your-tools = { path = "../../../", editable = true }

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF
		echo "rendered ${dst} (workspace path=../../../)"
		return 0
	fi
	render "${ROOT}/clear-your-tools/pyproject.toml.in" "$dst"
}

render_go_mod() {
	local src="$1"
	local dst="$2"
	local staging="${CYT_E2E_STAGING:-${TMPDIR:-/tmp}/cyt-e2e-${VERSION}}"
	sed -e "s/@CYT_RELEASE_VERSION@/${VERSION}/g" \
		-e "s|@CYT_E2E_STAGING@|${staging}|g" \
		"$src" >"$dst"
	echo "rendered ${dst} (staging=${staging})"
}

render_rust_cargo
render_python_pyproject
render_typescript_package
render_clear_your_tools_pyproject
render_go_mod "${ROOT}/go/go.mod.in" "${ROOT}/go/go.mod"
