# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)
export DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"      # rerank stage
export OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"    # llm stage

# Debug: reverse dry-run to anthropic.log
uv tool install 'clear-your-tools[all]' -p 3.13
cyt-rproxy serve --port 8834

curl -s http://localhost:8834/health
