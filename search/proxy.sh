# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)
export DEEPINFRA_API_KEY="..."      # rerank stage
export OPENROUTER_API_KEY="..."    # llm stage

# Debug: reverse dry-run to anthropic.log
# cyt-rproxy serve --debug --port 8834
uv run cyt-rproxy serve --port 8834

curl -s http://localhost:8834/health
