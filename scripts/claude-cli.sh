#!/usr/bin/env bash
PORT=8834

export ANTHROPIC_API_KEY=""
ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "cyt" -a "OPENROUTER_API_KEY" -w)"
export ANTHROPIC_AUTH_TOKEN
export ANTHROPIC_BASE_URL="http://localhost:${PORT}/openrouter"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek/deepseek-v4-pro"
export ANTHROPIC_DEFAULT_SONNET_MODEL="@preset/moonshotai-kimi-k2-6-fp4"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="google/gemini-3.1-flash-lite"
export CLAUDE_CODE_SUBAGENT_MODEL="google/gemini-3.1-flash-lite"

export ANTHROPIC_DEFAULT_SONNET_MODEL="moonshotai/kimi-k3" #  $3/$15
export ANTHROPIC_DEFAULT_HAIKU_MODEL="openai/gpt-5.6-luna" # $0.5/$3

"$HOME/.local/bin/claude" --model haiku 'say hi' -p

# PORT=8834
# export ANTHROPIC_BASE_URL="http://localhost:${PORT}/openrouter"
# claude --model haiku
