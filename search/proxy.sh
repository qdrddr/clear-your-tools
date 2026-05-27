# Required for pruning.pipeline: [rerank, llm] (keys in src/.env or exported)
export DEEPINFRA_API_KEY="..."      # rerank stage
export OPENROUTER_API_KEY="..."    # llm stage

# Debug: reverse dry-run to anthropic.log; forward appends decrypted bodies to forward.log
# uv run src/proxy.py serve --debug --port 8834
uv run src/proxy.py serve --port 8834

curl -s http://localhost:8834/health
