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
DEFAULT_REVERSE_PORT: int = 8000
DEFAULT_MCP_AGGREGATOR_PORT: int = 8000
DEFAULT_STRONG_MODEL: str = "gemini-3-flash"
DEFAULT_PRUNING_PIPELINE: list[str] = ["rerank"]
DEFAULT_STATS_DB_PATH: str = "~/.configs/cyt/stats.db"
DEFAULT_USER_CONFIG_PATH: Path = Path("~/.configs/cyt/config.yaml")
CWD_CONFIG_NAME: str = "config.yaml"
DEFAULT_SYSTEM_TOOL_POLICY: str = "prune_optional"
DEFAULT_MCP_TOOL_POLICY: str = "prune_all"
DEFAULT_DEBUG_LOG_MAX_BODY_BYTES: int = 1_048_576
DEFAULT_MIN_TOOLS_LLM_PRUNINER: int = 50
DEFAULT_MIN_TOOLS_RERANKER_PRUNINER: int = 10


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
        "system_tool_policy": DEFAULT_SYSTEM_TOOL_POLICY,
        "mcp_tool_policy": DEFAULT_MCP_TOOL_POLICY,
        "reranking_enabled": False,
    },
    "models": {
        "llm": {
            "minimum_tools": DEFAULT_MIN_TOOLS_LLM_PRUNINER,
        },
        "rerankers": {
            "minimum_tools": DEFAULT_MIN_TOOLS_RERANKER_PRUNINER,
        },
    },
    "pruning": {
        "pipeline": list(DEFAULT_PRUNING_PIPELINE),
        "per_tool": {},
    },
    "stats": {
        "enabled": True,
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
                "debug_log_max_body_bytes": DEFAULT_DEBUG_LOG_MAX_BODY_BYTES,
                "http2": {
                    "upstream": False,
                    "serve": False,
                    "ssl": {
                        "keyfile": None,
                        "certfile": None,
                    },
                },
            },
        },
    },
}


def _merged_config(config: dict[str, Any]) -> dict[str, Any]:
    """Layer *config* over built-in defaults for partial config dicts."""
    return _deep_merge(_DEFAULTS, config)


def resolve_config_path(path: Path | None = None) -> Path:
    """Resolve the config file path.

    Priority:
    1. Explicit *path* (e.g. ``--config``)
    2. ``./config.yaml`` in the current working directory
    3. ``~/.configs/cyt/config.yaml``
    """
    if path is not None:
        return path.expanduser()
    cwd_config = Path.cwd() / CWD_CONFIG_NAME
    if cwd_config.exists():
        return cwd_config
    return DEFAULT_USER_CONFIG_PATH.expanduser()


def _default_user_config_dict() -> dict[str, Any]:
    """Starter config written when no config file exists anywhere."""
    return _deep_merge(
        _DEFAULTS,
        {
            "network": {
                "proxy": {
                    "reverse": {
                        "port": 8834,
                        "upstreams": [
                            {
                                "upstream": "anthropic",
                                "url": "https://openrouter.ai/api",
                                "kind": "anthropic",
                            },
                        ],
                        "endpoints": ["anthropic"],
                    },
                },
            },
            "stats": {
                "database": {"path": DEFAULT_STATS_DB_PATH},
            },
        },
    )


def _write_default_user_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(
        _default_user_config_dict(),
        default_flow_style=False,
        sort_keys=False,
    )
    config_path.write_text(content, encoding="utf-8")


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` and layer it on top of built-in defaults.

    Missing keys are populated from built-in defaults so callers always receive
    a complete configuration dictionary. When no config file exists and no
    explicit path was given, creates ``~/.configs/cyt/config.yaml`` with
    built-in defaults.
    """
    config_path = resolve_config_path(path)
    user_config: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                user_config = loaded
    elif path is None and config_path == DEFAULT_USER_CONFIG_PATH.expanduser():
        _write_default_user_config(config_path)
        user_config = _default_user_config_dict()
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
    reverse_cfg = reverse_proxy_cfg(_merged_config(config)["network"]["proxy"])
    return int(reverse_cfg["port"])


def debug_log_max_body_bytes(config: dict[str, Any]) -> int:
    reverse_cfg = reverse_proxy_cfg(_merged_config(config)["network"]["proxy"])
    return int(reverse_cfg["debug_log_max_body_bytes"])


def proxy_http2_settings(config: dict[str, Any]) -> dict[str, Any]:
    merged = _merged_config(config)
    proxy_cfg = merged["network"]["proxy"]
    reverse_cfg = reverse_proxy_cfg(proxy_cfg)
    default_http2 = _DEFAULTS["network"]["proxy"]["reverse"]["http2"]
    http2_cfg = reverse_cfg.get("http2")
    if http2_cfg is None:
        http2_cfg = proxy_cfg.get("http2", default_http2)
    if isinstance(http2_cfg, bool):
        http2_cfg = {**default_http2, "upstream": http2_cfg}
    elif not isinstance(http2_cfg, dict):
        http2_cfg = default_http2
    ssl_cfg = http2_cfg.get("ssl", default_http2["ssl"])
    return {
        "http2_upstream": bool(http2_cfg["upstream"]),
        "http2_serve": bool(http2_cfg["serve"]),
        "ssl_keyfile": ssl_cfg.get("keyfile") or http2_cfg.get("ssl_keyfile"),
        "ssl_certfile": ssl_cfg.get("certfile") or http2_cfg.get("ssl_certfile"),
    }


def stats_db_path(config: dict[str, Any]) -> str:
    path = _merged_config(config)["stats"]["database"]["path"]
    return str(Path(path).expanduser())


def pruning_pipeline_from_config(config: dict[str, Any]) -> list[str]:
    pipeline = _merged_config(config)["pruning"]["pipeline"]
    if not isinstance(pipeline, list) or not all(isinstance(s, str) for s in pipeline):
        raise ValueError("pruning.pipeline must be a list of stage names")
    return pipeline


def llm_minimum_tools(config: dict[str, Any] | None = None) -> int:
    return int(_merged_config(config or load_config())["models"]["llm"]["minimum_tools"])


def reranker_minimum_tools(config: dict[str, Any] | None = None) -> int:
    return int(_merged_config(config or load_config())["models"]["rerankers"]["minimum_tools"])


def _llm_remote_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    entries = config.get("models", {}).get("llm", {}).get("remote", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def strong_model_entry(config: dict[str, Any]) -> dict[str, Any]:
    """Return the ``models.llm.remote`` entry referenced by ``stats.strong_model``."""
    merged = _merged_config(config)
    identifier = str(merged["stats"]["strong_model"])
    for entry in _llm_remote_entries(merged):
        nick = entry.get("nick")
        name = entry.get("name")
        if identifier == nick or identifier == name:
            return entry
    raise ValueError(
        f"stats.strong_model {identifier!r} does not match any models.llm.remote nick or name",
    )


def strong_model_name(config: dict[str, Any]) -> str:
    """Return the ``models.llm.remote`` nick for ``stats.strong_model``."""
    return str(strong_model_entry(config)["nick"])


def resolve_model(
    model_nick: str,
    model_kind: str,
    model_type: str,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[str, str | None, str | None]:
    """Return (model_name, api_key, base_url) for a given nick and type."""
    merged = _merged_config(config or load_config())
    for entry in merged.get("models", {}).get(model_kind, {}).get(model_type, []):
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
