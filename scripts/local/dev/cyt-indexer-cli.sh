#!/usr/bin/env bash

# Build the cyt-indexer release binary locally (nopatch; keeps Cargo.lock registry pins).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
env -u CARGO_TARGET_DIR "${ROOT}/scripts/local/dev/cargo-locked.sh" build -p cyt-indexer --release --locked

mkdir -p ./.catalog
# Extract the tools from the full example JSON
jq '.body.tools' debug/temp.json >./.catalog/input.json

# Build the application locally
./scripts/local/dev/workflow.sh all

# Build the catalog from the tools file
./target/release/cyt-indexer build tools --tools ./.catalog/input.json --output ./.catalog

# Extract the survivors from the full example JSON
jq '{
  json: .pruning.decomposed_catalog.build_index.json,
  md:   .pruning.decomposed_catalog.build_index.md
}' debug/full_example.json >.catalog/survivors.json

# Retrieve the tools from the catalog using the survivors file
./target/release/cyt-indexer retrieve tools \
	--catalog ./.catalog \
	--input ./.catalog/survivors.json \
	--output ./.catalog/out.json \
	--system-policy prune_optional \
	--mcp-policy prune_all \
	--tool-policy AskUserQuestion=always_include \
	--tool-policy mcp__fff__find_files=prune_optional \
	--tool-policy mcp__fff__multi_grep=prune_all_descriptions \
	--removed-output ./.catalog/removed.json

OPENROUTER_API_KEY="$(security find-generic-password -s "cyt" -a "OPENROUTER_API_KEY" -w)"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="$(security find-generic-password -s "cyt" -a "DEEPINFRA_API_KEY" -w)"
export DEEPINFRA_API_KEY

./scripts/local/dev/workflow.sh proxy --port 8834
# BM25 test
./scripts/local/dev/workflow.sh proxy --upstream https://openrouter.ai/api --upstream-kind anthropic --debug

# Skills
rm -rf ./.catalog/skills/
./scripts/local/dev/workflow.sh indexer build skills --skills ~/.claude/skills --output ./.catalog

rm -rf ./.catalog/skills/
./scripts/local/dev/workflow.sh indexer build skills --skills ~/.claude/skills --output ./.catalog \
	--window-mode word \
	--similarity-window 10 \
	--chunk-size 100 \
	--skip-window 0

# Optimal parameters
./scripts/local/dev/workflow.sh indexer build skills --skills ~/.claude/skills --output ./.catalog \
	--window-mode word \
	--similarity-window 100 \
	--chunk-size 500 \
	--skip-window 2

./scripts/local/dev/workflow.sh indexer retrieve skills \
	--catalog ./.catalog \
	--doc-id lean-ctx__skill \
	--query content \
	--node_id 4 \
	--output skill_out.json \
	--keep-all-headers

./scripts/local/dev/workflow.sh indexer retrieve skills \
	--catalog ./.catalog \
	--doc-id lean-ctx__skill \
	--query content \
	--chunk_id 3-4 \
	--chunk_id 7 \
	--output skill_out.json
