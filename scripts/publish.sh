#!/usr/bin/env bash
# update pyproject.toml version first

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
version="$(
	grep -E '^version[[:space:]]*=' "${ROOT}/pyproject.toml" |
		head -1 |
		sed -E 's/^version[[:space:]]*=[[:space:]]*"(.*)".*/\1/'
)"
export version
export tag="v${version}"

oco -n
git checkout main
git pull origin main
git tag "${tag}"
git push origin "${tag}"

git tag "sdk/go/v${version}"
git push origin "sdk/go/v${version}"

# git tag -f cyt-indexer-rust-v0.1.6
# git push -f origin cyt-indexer-rust-v0.1.6

# bash scripts/sync-version.sh
# export CARGO_REGISTRY_TOKEN="$(security find-generic-password -s "cyt" -a "CARGO_REGISTRY_TOKEN" -w)"
# cargo build -p cyt-indexer
# cargo test -p cyt-indexer
# cargo publish -p cyt-indexer --dry-run
# cargo publish
# gh workflow run publish-crates.yml --ref rust -f version=0.1.0

# git tag cyt-indexer-rust-v0.1.4
# git push origin cyt-indexer-rust-v0.1.4

# git tag cyt-indexer-sdk-v0.1.5
# git push origin cyt-indexer-sdk-v0.1.5
# (triggers PyPI + npm SDK publish workflows)

# npm login
# npm whoami
# npm view cyt-indexer-sdk
# cd sdk/typescript
# npm version 0.1.4 --no-git-tag-version
# npm ci
# npm test

# one-time:
npm login
npm whoami

cd sdk/typescript || exit
npm ci
npm run build:js
# Release publishes all platforms via publish-npm-sdk.yml (single fat package).
# Manual publish is only for bootstrapping or emergencies; you need every
# cyt-indexer-sdk.*.node in this directory before npm publish.
npm publish --access public
