PORT=8834

export OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export DEEPINFRA_API_KEY="$(security find-generic-password -s "nono" -a "DEEPINFRA_API_KEY" -w)"

# Start the proxy in the background (plain HTTP unless you enable http2.serve + TLS certs)
uv run cyt-rproxy serve --port "${PORT}" &
PROXY_PID=$!
trap 'kill "${PROXY_PID}" 2>/dev/null' EXIT
sleep 1

export ANTHROPIC_API_KEY=""
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export ANTHROPIC_BASE_URL="http://localhost:${PORT}/anthropic"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek/deepseek-v4-pro"
export ANTHROPIC_DEFAULT_SONNET_MODEL="@preset/moonshotai-kimi-k2-6-fp4"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="google/gemini-3-flash-preview"
export CLAUDE_CODE_SUBAGENT_MODEL="google/gemini-3-flash-preview"

$HOME/.local/bin/claude --model haiku -p "say hi!" 
