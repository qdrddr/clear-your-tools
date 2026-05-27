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
DEFAULT_REVERSE_PORT: int = 8000
DEFAULT_MCP_AGGREGATOR_PORT: int = 8000
DEFAULT_STRONG_MODEL: str = "google/gemini-3-flash-preview"
DEFAULT_PRUNING_PIPELINE: list[str] = ["rerank"]
DEFAULT_STATS_DB_PATH: str = "~/.configs/sca/stats.db"
DEFAULT_SYSTEM_TOOL_POLICY: str = "prune_all"
DEFAULT_MCP_TOOL_POLICY: str = "prune_all"
DEFAULT_VECTORDB_DIR: str = ".lancedb"

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
        "system_tool_policy": DEFAULT_SYSTEM_TOOL_POLICY,
        "mcp_tool_policy": DEFAULT_MCP_TOOL_POLICY,
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
        "dir": DEFAULT_VECTORDB_DIR,
    },
    "pruning": {
        "pipeline": list(DEFAULT_PRUNING_PIPELINE),
        "per_tool": {},
    },
    "stats": {
        "enabled": False,
        "store_full_tools": False,
        "strong_model": DEFAULT_STRONG_MODEL,
        "database": {
            "path": DEFAULT_STATS_DB_PATH,
        },
    },
    "network": {
        "proxy": {
            "reverse": {
                "port": DEFAULT_REVERSE_PORT,
                "http2": {
                    "upstream": False,
                    "serve": False,
                },
            },
        },
    },
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` and layer it on top of built-in defaults.

    Missing keys (including nested ones such as ``intent_router``
    sub-keys) are populated from the defaults so callers always receive
    a complete configuration dictionary.
    """
    config_path = path or Path(__file__).with_name("config.yaml")
    user_config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                user_config = loaded
    return _deep_merge(_DEFAULTS, user_config)


def reverse_proxy_cfg(proxy_cfg: dict[str, Any]) -> dict[str, Any]:
    reverse = proxy_cfg.get("reverse")
    if isinstance(reverse, dict):
        return reverse
    if proxy_cfg.get("upstreams") or proxy_cfg.get("endpoints"):
        return proxy_cfg
    raise ValueError("network.proxy.reverse must be configured")


def resolve_reverse_port(config: dict[str, Any], cli_port: int | None) -> int:
    if cli_port is not None:
        return cli_port
    proxy_cfg = config.get("network", {}).get("proxy", {})
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    return int(reverse_cfg.get("port", DEFAULT_REVERSE_PORT))


def proxy_http2_settings(config: dict[str, Any]) -> dict[str, Any]:
    proxy_cfg = config.get("network", {}).get("proxy", {})
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    http2_cfg = reverse_cfg.get("http2")
    if http2_cfg is None:
        http2_cfg = proxy_cfg.get("http2")
    if isinstance(http2_cfg, bool):
        return {
            "http2_upstream": http2_cfg,
            "http2_serve": False,
            "ssl_keyfile": None,
            "ssl_certfile": None,
        }
    if not isinstance(http2_cfg, dict):
        http2_cfg = {}
    ssl_cfg = http2_cfg.get("ssl") if isinstance(http2_cfg.get("ssl"), dict) else {}
    return {
        "http2_upstream": bool(http2_cfg.get("upstream", False)),
        "http2_serve": bool(http2_cfg.get("serve", False)),
        "ssl_keyfile": ssl_cfg.get("keyfile") or http2_cfg.get("ssl_keyfile"),
        "ssl_certfile": ssl_cfg.get("certfile") or http2_cfg.get("ssl_certfile"),
    }


def stats_db_path(config: dict[str, Any]) -> str:
    stats_cfg = config.get("stats", {})
    db_cfg = stats_cfg.get("database", {}) if isinstance(stats_cfg, dict) else {}
    path = db_cfg.get("path", DEFAULT_STATS_DB_PATH)
    return str(Path(path).expanduser())


def pruning_pipeline_from_config(config: dict[str, Any]) -> list[str]:
    pruning = config.get("pruning")
    pipeline = pruning.get("pipeline") if isinstance(pruning, dict) else None
    if pipeline is None:
        return list(DEFAULT_PRUNING_PIPELINE)
    if not isinstance(pipeline, list) or not all(isinstance(s, str) for s in pipeline):
        raise ValueError("pruning.pipeline must be a list of stage names")
    return pipeline


def strong_model_name(config: dict[str, Any]) -> str:
    stats = config.get("stats", {})
    if isinstance(stats, dict):
        configured = stats.get("strong_model")
        if isinstance(configured, str) and configured:
            return configured
    return DEFAULT_STRONG_MODEL


def resolve_model(
    model_nick: str,
    model_kind: str,
    model_type: str,
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
