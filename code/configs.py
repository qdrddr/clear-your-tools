"""Centralised configuration loader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Default fallbacks – single source of truth for hard-coded values
DEFAULT_INTENT_ROUTER_TOP_K: int = 10
DEFAULT_INTENT_ROUTER_THRESHOLD: float = 0.28
DEFAULT_EMBEDDING_MODEL_TYPE: str = "inprocess"
DEFAULT_EMBEDDING_MODEL_NICK: str = "all-MiniLM-L6-v2"
DEFAULT_LOCAL_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* over *base* without mutating either."""
    result: dict[str, Any] = {}
    for key in {*base, *overlay}:
        if key in base and key in overlay and isinstance(base[key], dict) and isinstance(overlay[key], dict):
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
                }
            ],
        },
    },
    "vectordb": {
        "dir": ".chroma_db",
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
