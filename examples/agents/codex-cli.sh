#!/usr/bin/env bash
# ~/.codex/config.toml

# model_provider = "custom_openai"
# [model_providers.custom_openai]
# name = "openai"
# env_key = "CODEX_OPENAI_API_KEY"
# base_url = "http://0.0.0.0:8834/openai/v1"

CODEX_OPENAI_API_KEY="$(security find-generic-password -s "nono" -a "OPENAI_API_KEY" -w)"
export CODEX_OPENAI_API_KEY
codex
