"""Embedding, intent-router, and vector-store configuration defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_INTENT_ROUTER_TOP_K: int = 10
DEFAULT_INTENT_ROUTER_THRESHOLD: float = 0.28
DEFAULT_EMBEDDING_MODEL_TYPE: str = "inprocess"
DEFAULT_EMBEDDING_MODEL_NICK: str = "all-MiniLM-L6-v2"
DEFAULT_LOCAL_MODEL_NAME: str = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_VECTORDB_DIR: str = ".lancedb"

FINGERPRINT_KEYS = (
    "embedding_model_name",
    "embedding_model_type",
    "embedding_base_url",
    "embedding_dimensions",
)

# Backward-compatible alias used by vector store metadata checks.
_FINGERPRINT_KEYS = FINGERPRINT_KEYS

EMBEDDING_CONFIG_DEFAULTS: dict[str, Any] = {
    "defaults": {
        "embedding_model_type": DEFAULT_EMBEDDING_MODEL_TYPE,
        "embedding_model_nick": DEFAULT_EMBEDDING_MODEL_NICK,
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
        "dir": DEFAULT_VECTORDB_DIR,
    },
}


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


def load_embedder_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` layered on top of embedding-specific defaults."""
    config_path = path or Path(__file__).resolve().parent.parent / "config.yaml"
    user_config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                user_config = loaded
    return _deep_merge(EMBEDDING_CONFIG_DEFAULTS, user_config)
