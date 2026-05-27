export ANTHROPIC_API_KEY=""
export ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"
export ANTHROPIC_BASE_URL="https://localhost:8834/anthropic"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek/deepseek-v4-pro"
export ANTHROPIC_DEFAULT_SONNET_MODEL="@preset/moonshotai-kimi-k2-6-fp4"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="google/gemini-3-flash-preview"
export CLAUDE_CODE_SUBAGENT_MODEL="google/gemini-3-flash-preview"

# Start the proxy
uv run src/proxy.py serve
$HOME/.local/bin/claude --model haiku -p "say hi!"
