#!/usr/bin/env bash

# Build the cyt-indexer release binary locally
env -u CARGO_TARGET_DIR cargo build -p cyt-indexer --release

mkdir -p ./.catalog
# Extract the tools from the full example JSON
jq '.body.tools' debug/temp.json >./.catalog/input.json

# Build the application locally
./search/local-dev.sh all

# Build the catalog from the tools file
./target/release/cyt-indexer build --tools ./.catalog/input.json --output ./.catalog

# Extract the survivors from the full example JSON
jq '{
  json: .pruning.decomposed_catalog.build_index.json,
  md:   .pruning.decomposed_catalog.build_index.md
}' debug/full_example.json >.catalog/survivors.json

# Retrieve the tools from the catalog using the survivors file
./target/release/cyt-indexer retrieve \
	--catalog ./.catalog \
	--input ./.catalog/survivors.json \
	--output ./.catalog/out.json \
	--system-policy prune_optional \
	--mcp-policy prune_all \
	--tool-policy AskUserQuestion=always_include \
	--tool-policy mcp__fff__find_files=prune_optional \
	--tool-policy mcp__fff__multi_grep=prune_all_descriptions \
	--removed-output ./.catalog/removed.json

OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"
export DEEPINFRA_API_KEY

./search/local-dev.sh proxy --port 8834
