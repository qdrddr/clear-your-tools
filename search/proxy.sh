#!/usr/bin/env bash
# Debug: reverse dry-run to anthropic.log
# install cyt
uv tool install 'clear-your-tools[all]' -p 3.14
uv tool install 'clear-your-tools[all]==v0.5.3' -p 3.14 --force
uv tool upgrade 'clear-your-tools[all]==v0.5.3' -p 3.14

# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)

OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export OPENROUTER_API_KEY
DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"
export DEEPINFRA_API_KEY

# Start the proxy in the background (plain HTTP unless you enable http2.serve + TLS certs)
cyt proxy --upstream https://api.openai.com --upstream-kind openai
cyt proxy --upstream https://api.anthropic.com --upstream-kind anthropic
cyt proxy --upstream https://openrouter.ai/api --upstream-kind anthropic

export PORT=8834
curl -s "http://localhost:${PORT}/health"

# uv tool uninstall clear-your-tools
# uv tool install 'clear-your-tools[all]'
# Retrieve up-to-date documentation for latest versions of qdrddr/clear-your-tools framework using Context7 library. Provide a brief explanation of its purpose. Keep it short and simple.

# Fetch current interface documentation for the latest version of the qdrddr/clear-your-tools library. Resolve the library ID, query the docs—not training data—and prefer official sources with version-specific IDs. Requests a brief, simple explanation of its primary usecase using current, accurate information.
# I heard you're supposed to do a lookup in two hops before answering — first turn what I called the thing plus my whole message into an internal handle (breaking ties by label fit, scoreboard, and upstream steward over mirrors), then fetch passages with that handle using the same message. Does mentioning a release train like v0.4.4 in my message change which handle clear your tools wins?

# According to docs, query how do I configure App Programming Interface for qdrddr/clear-your-tools, does it rely on Svelte or another programming language? Exact, resolve relevance ranking/scores. Include samples, cite official exact versions and its purpose.
# I'm wiring on an agent that has to pull fresh package material before it replies, not from memory. The template I was told is: first call resolve-id with the package name extracted from what I queried plus my entire message as the query, then from that list pick the best match by closest name fit, higher benchmark score, and official maintainer over forks and no AST or Agents — and if I named a semver in the message, go toward a version ID if one exists. Second hop is query-docs with the chosen appId and the same message as the query. Does dropping something similar v0.4.4 into my request change which appId wins for clear-your-tools and it spurpose? And how do I get what are languages used in the app?

uv run src/cyt/proxy/cli.py hook --debug --prompt ""
