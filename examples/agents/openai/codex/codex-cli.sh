#!/usr/bin/env bash
# ~/.codex/config.toml

# model_provider = "custom_openai"
# [model_providers.custom_openai]
# name = "openai"
# env_key = "CODEX_OPENAI_API_KEY"
# base_url = "http://127.0.0.1:8834/openai/v1"

CODEX_OPENAI_API_KEY="$(security find-generic-password -s "nono" -a "OPENAI_API_KEY" -w)"
export CODEX_OPENAI_API_KEY

codex -m gpt-5.4-mini \
  -c 'model_provider="cyt"' \
  -c 'model_providers.cyt.name="cyt-proxy"' \
  -c 'model_providers.cyt.base_url="http://127.0.0.1:8834/openai/v1"' \
  -c 'model_providers.cyt.wire_api="responses"' \
  -c 'model_providers.cyt.env_key="CODEX_OPENAI_API_KEY"'