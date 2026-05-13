"""Centralised configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values, load_dotenv

# Load .env so API keys (e.g. OPENROUTER_API_KEY) are available.
# Shell environment takes precedence.
_env_path = Path(__file__).with_name(".env")
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)

# Default fallbacks - single source of truth for hard-coded values
DEFAULT_INTENT_ROUTER_TOP_K: int = 10
DEFAULT_INTENT_ROUTER_THRESHOLD: float = 0.28
DEFAULT_EMBEDDING_MODEL_TYPE: str = "inprocess"
DEFAULT_EMBEDDING_MODEL_NICK: str = "all-MiniLM-L6-v2"
DEFAULT_LOCAL_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"

_FINGERPRINT_KEYS = (
    "embedding_model_name",
    "embedding_model_type",
    "embedding_base_url",
    "embedding_dimensions",
)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* over *base* without mutating either."""
    result: dict[str, Any] = {}
    for key in {*base, *overlay}:
        if (
            key in base
            and key in overlay
            and isinstance(base[key], dict)
            and isinstance(overlay[key], dict)
        ):
            result[key] = _deep_merge(base[key], overlay[key])
        elif key in overlay:
            result[key] = overlay[key]
        else:
            result[key] = base[key]
    return result


_DEFAULTS: dict[str, Any] = {
    "defaults": {
        "is_persistent": True,
        "embedding_model_type": DEFAULT_EMBEDDING_MODEL_TYPE,
        "embedding_model_nick": DEFAULT_EMBEDDING_MODEL_NICK,
        "reranking_enabled": False,
        "intent_router": {
            "top_k": DEFAULT_INTENT_ROUTER_TOP_K,
            "threshold": DEFAULT_INTENT_ROUTER_THRESHOLD,
        },
    },
    "models": {
        "embeddings": {
            "inprocess": [
                {
                    "name": DEFAULT_LOCAL_MODEL_NAME,
                    "nick": DEFAULT_EMBEDDING_MODEL_NICK,
                },
            ],
        },
    },
    "vectordb": {
        "dir": ".lancedb",
    },
}


def load_config() -> dict[str, Any]:
    """Load ``config.yaml`` and layer it on top of built-in defaults.

    Missing keys (including nested ones such as ``intent_router``
    sub-keys) are populated from the defaults so callers always receive
    a complete configuration dictionary.
    """
    config_path = Path(__file__).with_name("config.yaml")
    user_config: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                user_config = loaded
    return _deep_merge(_DEFAULTS, user_config)


def resolve_model(
    model_nick: str, model_kind: str, model_type: str,
) -> tuple[str, str | None, str | None]:
    """Return (model_name, api_key, base_url) for a given nick and type."""
    config = load_config()
    for entry in config.get("models", {}).get(model_kind, {}).get(model_type, []):
        if entry.get("nick") == model_nick:
            provider = entry.get("provider", None)
            full_model_name = entry.get("name")
            if model_type == "remote":
                env_values = dotenv_values(_env_path)
                key_var_name = entry.get("key_var_name")
                api_key_value = None
                if key_var_name in env_values:
                    api_key_value = env_values[key_var_name]
                base_url = entry.get("base_url")
                if provider:
                    return f"{provider}/{full_model_name}", api_key_value, base_url
                else:
                    raise ValueError(
                        f"Unknown remote provider for nick: {model_nick}, kind: {model_kind}, type: {model_type} in LiteLLM format",
                    )
            else:
                return f"{full_model_name}", None, None
    raise ValueError(f"Unknown model nick: {model_nick}, kind: {model_kind}, type: {model_type}")
