#!/usr/bin/env bash
export PORT=8834

export ANTHROPIC_API_KEY=""
ANTHROPIC_AUTH_TOKEN="$(security find-generic-password -s "cyt" -a "OPENROUTER_API_KEY" -w)"
export ANTHROPIC_AUTH_TOKEN
export ANTHROPIC_BASE_URL="http://localhost:${PORT}/openrouter"
export ANTHROPIC_DEFAULT_OPUS_MODEL="deepseek/deepseek-v4-pro"
export ANTHROPIC_DEFAULT_SONNET_MODEL="@preset/moonshotai-kimi-k2-6-fp4"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="google/gemini-3-flash-preview"
export CLAUDE_CODE_SUBAGENT_MODEL="google/gemini-3-flash-preview"

"$HOME/.local/bin/claude" --model haiku 'say hi' -p
