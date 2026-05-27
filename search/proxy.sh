
# Debug: reverse dry-run to anthropic.log
uv tool install 'clear-your-tools[all]' -p 3.13
uv tool upgrade 'clear-your-tools[all]' -p 3.13



PORT=8834
# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)

export OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"

# Start the proxy in the background (plain HTTP unless you enable http2.serve + TLS certs)
uv run cyt-rproxy serve --port "${PORT}"

curl -s http://localhost:8834/health
