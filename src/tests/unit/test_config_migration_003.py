#!/usr/bin/env python3
"""Tests for revision 003 — model provider registry migration."""

from __future__ import annotations

from typing import Any

from cyt.migrations.versions import load_revision_modules


def _upgrade_003(cfg: dict[str, Any]) -> dict[str, Any]:
    module = next(m for m in load_revision_modules() if m.revision == "003_model_provider_registry")
    return module.upgrade(cfg, scope="global")


def test_extracts_inline_provider_fields_to_registry() -> None:
    cfg = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "nick": "mercury-2",
                        "name": "inception/mercury-2",
                        "provider": "openrouter",
                        "provider_nick": "openrouter-ai",
                        "key_var_name": "OPENROUTER_API_KEY",
                    },
                ],
            },
        },
    }
    out = _upgrade_003(cfg)
    entry = out["models"]["llm"]["remote"][0]
    assert entry["provider_nick"] == "openrouter-ai"
    assert "key_var_name" not in entry
    assert "provider" not in entry
    providers = {p["provider_nick"]: p for p in out["models"]["providers"]}
    assert providers["openrouter-ai"]["key_var_name"] == "OPENROUTER_API_KEY"


def test_normalizes_legacy_nested_provider_map() -> None:
    cfg = {
        "models": {
            "providers": [
                {
                    "openrouter-ai": {
                        "provider": "openrouter",
                        "key_var_name": "OPENROUTER_API_KEY",
                    },
                },
            ],
        },
    }
    out = _upgrade_003(cfg)
    providers = {p["provider_nick"]: p for p in out["models"]["providers"]}
    assert providers["openrouter-ai"]["provider"] == "openrouter"
