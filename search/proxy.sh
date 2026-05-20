# Required for pruning_pipeline: [rerank, llm] (keys in src/.env or exported)
export DEEPINFRA_API_KEY="..."      # rerank stage
export OPENROUTER_API_KEY="..."    # llm stage

# Debug: no upstream; appends snapshots to anthropic.log; prints query + decomposed counts
# uv run src/proxy.py --debug --port 8834
uv run src/proxy.py --port 8834

curl -s http://localhost:8834/health
