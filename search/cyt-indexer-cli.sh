#!/usr/bin/env bash

# Build the cyt-indexer release binary locally
env -u CARGO_TARGET_DIR cargo build -p cyt-indexer --release

# Extract the tools from the full example JSON
jq '.body.tools' debug/full_example.json >/tmp/tools.json

# Build the catalog from the tools file
./target/release/cyt-indexer build --tools /tmp/tools.json --output ./.catalog

# Extract the survivors from the full example JSON
jq '{
  json: .pruning.decomposed_catalog.rerank.json,
  md:   .pruning.decomposed_catalog.rerank.md
}' debug/full_example.json >.catalog/survivors.json

# Retrieve the tools from the catalog using the survivors file
./target/release/cyt-indexer retrieve \
	--catalog ./.catalog \
	--input ./.catalog/survivors.json \
	--output ./.catalog/out.json \
	--system-policy prune_optional \
	--mcp-policy prune_all \
	--tool-policy AskUserQuestion=always_include \
	--removed-output ./.catalog/removed.json

./search/local-dev.sh all

OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"
export DEEPINFRA_API_KEY

./search/local-dev.sh proxy --port 8834
