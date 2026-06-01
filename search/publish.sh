#!/usr/bin/env bash
# update pyproject.toml version first

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(
	grep -E '^version[[:space:]]*=' "${ROOT}/pyproject.toml" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
)"
tag="v${version}"

oco -n
git checkout main
git pull origin main
git tag "${tag}"
git push origin "${tag}"

# bash search/sync-version.sh
# export CARGO_REGISTRY_TOKEN="$(security find-generic-password -s "nono" -a "CARGO_REGISTRY_TOKEN" -w)"
# cargo build -p cyt-indexer
# cargo test -p cyt-indexer
# cargo publish -p cyt-indexer --dry-run
# cargo publish
# gh workflow run publish-crates.yml --ref rust -f version=0.1.0

# git tag cyt-indexer-rust-v0.1.4
# git push origin cyt-indexer-rust-v0.1.4

# git tag cyt-indexer-sdk-v0.1.3
# git push origin cyt-indexer-sdk-v0.1.3
