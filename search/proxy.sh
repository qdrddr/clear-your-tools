#!/usr/bin/env bash
# Debug: reverse dry-run to anthropic.log
# install cyt
uv tool install 'clear-your-tools[all]' -p 3.13
uv tool upgrade 'clear-your-tools[all]==v0.0.6' -p 3.13

# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)


export OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"

# Start the proxy in the background (plain HTTP unless you enable http2.serve + TLS certs)
cyt proxy --upstream https://api.openai.com --upstream-kind openai
cyt proxy --upstream https://api.anthropic.com --upstream-kind anthropic
cyt proxy --upstream https://openrouter.ai/api --upstream-kind anthropic

export PORT=8834
curl -s "http://localhost:${PORT}/health"
