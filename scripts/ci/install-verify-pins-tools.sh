#!/usr/bin/env bash
# Install CLI tools required by scripts/deps/verify-pins.sh (Rust CycloneDX SBOM).
#
# Snyk vulnerability scanning runs in Snyk Cloud; the Snyk CLI is not installed in CI.
#
# Usage: ./scripts/ci/install-verify-pins-tools.sh
set -euo pipefail

CYCLONEDX_VERSION="${CYCLONEDX_VERSION:-0.5.9}"

if ! command -v cargo-cyclonedx >/dev/null 2>&1; then
	echo "Installing cargo-cyclonedx ${CYCLONEDX_VERSION}..."
	cargo install cargo-cyclonedx --locked --version "${CYCLONEDX_VERSION}"
else
	echo "cargo-cyclonedx already installed: $(cargo cyclonedx --version 2>&1 | head -1)"
fi

require_cmd() {
	command -v "$1" >/dev/null 2>&1 || {
		echo "error: missing required command after install: $1" >&2
		exit 1
	}
}

require_cmd jq
require_cmd cargo-cyclonedx

echo "verify-pins tools ready."
