brew install lightonai/tap/colgrep

colgrep init

colgrep "base64 for parquet" -k 15 --include "*.json" --exclude-dir "**/full/*"  --no-pool -y --json

#uv tool install "next-plaid-client[cli]" -p 3.13
#next-plaid index create mcp
#next-plaid document add mcp --text "hello world"
#next-plaid search docs "hello"