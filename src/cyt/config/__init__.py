"""Centralised configuration loader."""

from __future__ import annotations

import copy
import importlib.resources
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


BUNDLED_DEFAULTS_NAME = "defaults.yaml"
USER_ENV_PATH = Path("~/.config/cyt/.env").expanduser()
CWD_ENV_PATH = Path.cwd() / ".env"

_proxy_env_loaded = False


def load_proxy_env() -> None:
    """Load API keys from ``./.env``, then ``~/.config/cyt/.env``.

    Variables already set in the process environment are never overwritten.
    """
    global _proxy_env_loaded
    if _proxy_env_loaded:
        return
    for path in (CWD_ENV_PATH, USER_ENV_PATH):
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)
    _proxy_env_loaded = True


load_proxy_env()

# Default fallbacks - single source of truth for hard-coded values
DEFAULT_REVERSE_PORT: int = 8000
DEFAULT_PRUNING_PIPELINE: list[str] = ["rerank"]
DEFAULT_STATS_DB_PATH: str = "~/.config/cyt/stats.db"
DEFAULT_USER_CONFIG_PATH: Path = Path("~/.config/cyt/config.yaml")
CWD_CONFIG_NAME: str = "config.yaml"
ToolPolicy = Literal["always_include", "prune_optional", "prune_all"]
DEFAULT_SYSTEM_TOOL_POLICY: ToolPolicy = "prune_optional"
DEFAULT_MCP_TOOL_POLICY: ToolPolicy = "prune_all"
DEFAULT_DEBUG_LOG_MAX_BODY_BYTES: int = 1_048_576
DEFAULT_DEBUG_LOG_DIR: str = ".debug"
DEFAULT_MIN_TOOLS_PRUNING: int = 50
DEFAULT_MIN_TOOLS_LLM_PRUNINER: int = DEFAULT_MIN_TOOLS_PRUNING
DEFAULT_MIN_TOOLS_RERANKER_PRUNINER: int = DEFAULT_MIN_TOOLS_PRUNING
DEFAULT_BM25_INDEX_DIR: str = "~/.config/cyt/bm25"
DEFAULT_BM25_STEM_LANGUAGE: str = "english"
DEFAULT_BM25_STOPWORDS: str = "en"
VALID_PRUNING_STAGES: frozenset[str] = frozenset({"rerank", "llm", "bm25"})


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
        "bm25": {
            "index_dir": DEFAULT_BM25_INDEX_DIR,
            "mmap": True,
            "stem_language": DEFAULT_BM25_STEM_LANGUAGE,
            "stopwords": DEFAULT_BM25_STOPWORDS,
        },
    },
    "pruning": {
        "pipeline": list(DEFAULT_PRUNING_PIPELINE),
        "per_tool": {},
        "policy": {
            "system_tool": DEFAULT_SYSTEM_TOOL_POLICY,
            "mcp_tool": DEFAULT_MCP_TOOL_POLICY,
            "minimum_tools": DEFAULT_MIN_TOOLS_PRUNING,
        },
        "rerank": {
            "model": {
                "remote": {
                    "model_nick": "rerank-qwen3-8b",
                },
            },
        },
        "llm": {
            "model": {
                "remote": {
                    "model_nick": "mercury-2",
                },
            },
        },
        "bm25": {
            "index_dir": DEFAULT_BM25_INDEX_DIR,
        },
    },
    "stats": {
        "enabled": True,
        "store_full_tools": False,
        "database": {
            "path": DEFAULT_STATS_DB_PATH,
        },
    },
    "network": {
        "proxy": {
            "reverse": {
                "port": DEFAULT_REVERSE_PORT,
                "debug_log_max_body_bytes": DEFAULT_DEBUG_LOG_MAX_BODY_BYTES,
                "debug_log_dir": DEFAULT_DEBUG_LOG_DIR,
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
    3. ``~/.config/cyt/config.yaml``
    """
    if path is not None:
        return path.expanduser()
    cwd_config = Path.cwd() / CWD_CONFIG_NAME
    if cwd_config.exists():
        return cwd_config
    return DEFAULT_USER_CONFIG_PATH.expanduser()


def bundled_user_config_sections() -> dict[str, Any]:
    """Packaged-default sections that belong in the on-disk user config file."""
    bundled = _load_bundled_defaults_yaml()
    result: dict[str, Any] = {}

    pruning = bundled.get("pruning")
    if isinstance(pruning, dict) and "per_tool" in pruning:
        result["pruning"] = {"per_tool": copy.deepcopy(pruning["per_tool"])}

    network = bundled.get("network")
    if isinstance(network, dict):
        proxy = network.get("proxy")
        if isinstance(proxy, dict):
            reverse = proxy.get("reverse")
            if isinstance(reverse, dict):
                reverse_overlay: dict[str, Any] = {}
                for key in ("debug_log_dir", "debug_log_max_body_bytes", "http2"):
                    if key in reverse:
                        reverse_overlay[key] = copy.deepcopy(reverse[key])
                if reverse_overlay:
                    result.setdefault("network", {}).setdefault("proxy", {})["reverse"] = (
                        reverse_overlay
                    )

    return result


def _force_deep_assign(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Assign *source* onto *target*, replacing dict subtrees (including empty ones)."""
    for key, value in source.items():
        if isinstance(value, dict):
            if not value:
                target[key] = {}
                continue
            child = target.get(key)
            if not isinstance(child, dict):
                child = {}
                target[key] = child
            _force_deep_assign(child, value)
            continue
        target[key] = value


def _default_user_config_dict() -> dict[str, Any]:
    """Starter config written when no config file exists anywhere."""
    return _deep_merge(
        _deep_merge(_DEFAULTS, bundled_user_config_sections()),
        {
            "network": {
                "proxy": {
                    "reverse": {
                        "port": 8834,
                        "upstreams": [
                            {
                                "upstream": "anthropic",
                                "url": "https://api.anthropic.com",
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


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


def default_model_nick(provider: str, name: str) -> str:
    """Build a default model nick from provider and LiteLLM model name."""
    raw = f"{provider}-{name}"
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()


def save_user_config(
    path: Path,
    overlay: dict[str, Any],
    *,
    apply_bundled_sections: bool = False,
) -> None:
    """Deep-merge *overlay* onto an existing user config file and write YAML."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = _load_yaml_dict(path) if path.exists() else {}
    bundled_sections = bundled_user_config_sections() if apply_bundled_sections else {}
    combined_overlay = _deep_merge(bundled_sections, overlay) if apply_bundled_sections else overlay
    merged = _deep_merge(existing, combined_overlay)
    if apply_bundled_sections:
        _force_deep_assign(merged, bundled_sections)
    content = yaml.dump(merged, default_flow_style=False, sort_keys=False)
    path.write_text(content, encoding="utf-8")


def resolve_setup_config_path(path: Path | None = None) -> Path:
    """Config path for ``proxy setup`` (defaults to ``~/.config/cyt/config.yaml``)."""
    if path is not None:
        return path.expanduser()
    return DEFAULT_USER_CONFIG_PATH.expanduser()


def load_bundled_defaults_yaml() -> dict[str, Any]:
    """Load packaged ``defaults.yaml`` (public wrapper)."""
    return _load_bundled_defaults_yaml()


def _load_bundled_defaults_yaml() -> dict[str, Any]:
    """Load packaged ``defaults.yaml`` (wheel) or sibling file (editable install)."""
    try:
        ref = importlib.resources.files("cyt.config").joinpath(BUNDLED_DEFAULTS_NAME)
        if ref.is_file():
            loaded = yaml.safe_load(ref.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
    except (FileNotFoundError, ModuleNotFoundError, TypeError, OSError):
        pass
    bundled_path = Path(__file__).with_name(BUNDLED_DEFAULTS_NAME)
    if bundled_path.exists():
        return _load_yaml_dict(bundled_path)
    return {}


def _config_with_bundled_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    """Layer built-in defaults, bundled ``defaults.yaml``, then *user_config*."""
    merged = _deep_merge(_DEFAULTS, _load_bundled_defaults_yaml())
    return _deep_merge(merged, user_config)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load ``config.yaml`` and layer it on top of built-in defaults.

    The bundled ``defaults.yaml`` (when present) is merged before the resolved
    user/cwd file so partial overrides in ``~/.config/cyt/config.yaml`` keep
    model and pipeline settings from the package defaults.

    Missing keys are populated from built-in defaults so callers always receive
    a complete configuration dictionary. When no config file exists and no
    explicit path was given, creates ``~/.config/cyt/config.yaml`` with
    built-in defaults.
    """
    config_path = resolve_config_path(path)
    user_config: dict[str, Any] = {}
    if config_path.exists():
        user_config = _load_yaml_dict(config_path)
    elif path is None and config_path == DEFAULT_USER_CONFIG_PATH.expanduser():
        _write_default_user_config(config_path)
        user_config = _default_user_config_dict()
    return _config_with_bundled_defaults(user_config)


def load_user_config_overlay(path: Path | None = None) -> dict[str, Any]:
    """Load on-disk ``config.yaml`` without merging bundled or built-in defaults."""
    config_path = resolve_config_path(path)
    if not config_path.exists():
        return {}
    return _load_yaml_dict(config_path)


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


def reverse_debug_log_dir(config: dict[str, Any]) -> Path:
    reverse_cfg = reverse_proxy_cfg(_merged_config(config)["network"]["proxy"])
    return Path(str(reverse_cfg["debug_log_dir"])).expanduser()


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


def _resolve_pruning_stages(
    configured: list[str],
    tool_count: int,
    *,
    rerank_min: int,
    llm_min: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    effective: list[str] = []
    skipped: list[dict[str, Any]] = []
    for stage in configured:
        if stage not in VALID_PRUNING_STAGES:
            raise ValueError(f"unknown pruning stage: {stage}")
        if stage == "bm25":
            effective.append("bm25")
            continue
        minimum = rerank_min if stage == "rerank" else llm_min
        if tool_count >= minimum:
            effective.append(stage)
        else:
            skipped.append(
                {"stage": stage, "tool_count": tool_count, "minimum_tools": minimum},
            )
    return effective, skipped


def _warn_pruning_pipeline_adjustments(
    configured: list[str],
    effective: list[str],
    skipped_stages: list[dict[str, Any]],
) -> None:
    if not skipped_stages or effective == configured:
        return
    for skip in skipped_stages:
        logger.warning(
            "Pruning stage %r skipped: %d tools below pruning.policy.minimum_tools %d",
            skip["stage"],
            skip["tool_count"],
            skip["minimum_tools"],
        )
    if effective == ["bm25"] and "bm25" not in configured:
        logger.warning(
            "Pruning pipeline fallback: configured %s -> %s",
            configured,
            effective,
        )


def effective_pruning_pipeline(
    config: dict[str, Any],
    tool_count: int,
    *,
    configured_pipeline: list[str] | None = None,
) -> list[str]:
    """Resolve configured pipeline, substituting bm25 when remote stages cannot run."""
    configured = (
        configured_pipeline
        if configured_pipeline is not None
        else pruning_pipeline_from_config(config)
    )
    if not configured:
        return ["bm25"]

    effective, skipped = _resolve_pruning_stages(
        configured,
        tool_count,
        rerank_min=reranker_minimum_tools(config),
        llm_min=llm_minimum_tools(config),
    )
    if not effective:
        effective = ["bm25"]
    _warn_pruning_pipeline_adjustments(configured, effective, skipped)
    return effective


def _nested_dict_value(root: dict[str, Any], *keys: str) -> object | None:
    if not keys:
        return None
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


_PIPELINE_STAGE_MODEL_KEYS: dict[str, tuple[str, str]] = {
    "rerank": ("rerankers", "reranking_model_nick"),
    "llm": ("llm", "llm_model_nick"),
}


def _pruning_section(config: dict[str, Any]) -> dict[str, Any]:
    pruning = _merged_config(config).get("pruning", {})
    return pruning if isinstance(pruning, dict) else {}


def _resolve_user_then_merged(
    merged: dict[str, Any],
    user: dict[str, Any],
    *,
    new_keys: tuple[str, ...],
    legacy_keys: tuple[str, ...],
) -> object | None:
    """Prefer explicit user keys; when both exist in merged layers, prefer *new_keys*."""
    user_new = _nested_dict_value(user, *new_keys)
    user_legacy = _nested_dict_value(user, *legacy_keys)
    if user_new is not None:
        return user_new
    if user_legacy is not None:
        return user_legacy
    merged_new = _nested_dict_value(merged, *new_keys)
    merged_legacy = _nested_dict_value(merged, *legacy_keys)
    if merged_new is not None and merged_legacy is not None:
        return merged_new
    if merged_new is not None:
        return merged_new
    return merged_legacy


def _user_overlay_for_config(
    config: dict[str, Any] | None,
    *,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if user_config is not None:
        return user_config
    if config is None:
        return load_user_config_overlay()
    return config


def pruning_system_tool_policy(
    config: dict[str, Any],
    *,
    user_config: dict[str, Any] | None = None,
) -> ToolPolicy:
    """Resolve system tool policy: ``pruning.policy.system_tool`` then legacy ``defaults``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    policy = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", "policy", "system_tool"),
        legacy_keys=("defaults", "system_tool_policy"),
    )
    if policy is None:
        return DEFAULT_SYSTEM_TOOL_POLICY
    return cast(ToolPolicy, policy)


def pruning_mcp_tool_policy(
    config: dict[str, Any],
    *,
    user_config: dict[str, Any] | None = None,
) -> ToolPolicy:
    """Resolve MCP tool policy: ``pruning.policy.mcp_tool`` then legacy ``defaults``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    policy = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", "policy", "mcp_tool"),
        legacy_keys=("defaults", "mcp_tool_policy"),
    )
    if policy is None:
        return DEFAULT_MCP_TOOL_POLICY
    return cast(ToolPolicy, policy)


def pruning_stage_model_nick(
    config: dict[str, Any],
    stage: Literal["rerank", "llm"],
    *,
    user_config: dict[str, Any] | None = None,
) -> str | None:
    """Resolve stage model nick from ``pruning.<stage>`` then legacy ``defaults.remote``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    _, legacy_key = _PIPELINE_STAGE_MODEL_KEYS[stage]
    nick = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", stage, "model", "remote", "model_nick"),
        legacy_keys=("defaults", "remote", legacy_key),
    )
    return str(nick) if nick is not None else None


def _bm25_index_dir_resolved(
    merged: dict[str, Any],
    user: dict[str, Any],
) -> str:
    index_dir = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", "bm25", "index_dir"),
        legacy_keys=("models", "bm25", "index_dir"),
    )
    return str(index_dir) if index_dir is not None else DEFAULT_BM25_INDEX_DIR


def _bm25_settings(
    config: dict[str, Any],
    *,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    models_bm25 = merged.get("models", {}).get("bm25", {})
    settings: dict[str, Any] = dict(models_bm25) if isinstance(models_bm25, dict) else {}
    settings["index_dir"] = _bm25_index_dir_resolved(merged, user)
    return settings


def bm25_index_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = config or load_config()
    user = load_user_config_overlay() if config is None else None
    path = _bm25_settings(cfg, user_config=user).get("index_dir", DEFAULT_BM25_INDEX_DIR)
    return Path(str(path)).expanduser()


def bm25_mmap_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = config or load_config()
    user = load_user_config_overlay() if config is None else None
    return bool(_bm25_settings(cfg, user_config=user).get("mmap", True))


def bm25_stem_language(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    user = load_user_config_overlay() if config is None else None
    value = _bm25_settings(cfg, user_config=user).get("stem_language", DEFAULT_BM25_STEM_LANGUAGE)
    return str(value)


def bm25_stopwords(config: dict[str, Any] | None = None) -> str:
    cfg = config or load_config()
    user = load_user_config_overlay() if config is None else None
    value = _bm25_settings(cfg, user_config=user).get("stopwords", DEFAULT_BM25_STOPWORDS)
    return str(value)


def _user_pruning_pipeline(user_config: dict[str, Any]) -> list[str]:
    pruning = user_config.get("pruning")
    if not isinstance(pruning, dict):
        return []
    pipeline = pruning.get("pipeline")
    if not isinstance(pipeline, list):
        return []
    return pipeline


def _user_remote_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    defaults = user_config.get("defaults")
    if not isinstance(defaults, dict):
        return {}
    remote = defaults.get("remote")
    return remote if isinstance(remote, dict) else {}


def _user_stage_model_nick(user_config: dict[str, Any], stage: str) -> str | None:
    pruning = user_config.get("pruning")
    if isinstance(pruning, dict):
        stage_cfg = pruning.get(stage, {})
        if isinstance(stage_cfg, dict):
            model = stage_cfg.get("model", {})
            if isinstance(model, dict):
                remote = model.get("remote", {})
                if isinstance(remote, dict):
                    nick = remote.get("model_nick")
                    if nick:
                        return str(nick)
    stage_keys = _PIPELINE_STAGE_MODEL_KEYS.get(stage)
    if stage_keys is None:
        return None
    _, nick_key = stage_keys
    legacy = _user_remote_defaults(user_config).get(nick_key)
    return str(legacy) if legacy else None


def _remote_model_configured(
    user_config: dict[str, Any],
    *,
    model_kind: str,
    model_nick: str,
) -> bool:
    models = user_config.get("models")
    if not isinstance(models, dict):
        return False
    kind_models = models.get(model_kind, {})
    if not isinstance(kind_models, dict):
        return False
    remote_entries = kind_models.get("remote", [])
    if not isinstance(remote_entries, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("nick") == model_nick for entry in remote_entries
    )


def _remote_pruning_stage_configured(user_config: dict[str, Any], stage: str) -> bool:
    stage_keys = _PIPELINE_STAGE_MODEL_KEYS.get(stage)
    if stage_keys is None:
        return False
    model_kind, _ = stage_keys
    model_nick = _user_stage_model_nick(user_config, stage)
    if not model_nick:
        return False
    return _remote_model_configured(user_config, model_kind=model_kind, model_nick=model_nick)


def remote_pruning_pipeline_configured(user_config: dict[str, Any]) -> bool:
    """True when ``config.yaml`` explicitly configures a remote rerank/llm pruning stage."""
    pipeline = _user_pruning_pipeline(user_config)
    if not pipeline:
        return False
    return any(_remote_pruning_stage_configured(user_config, stage) for stage in pipeline)


def _remote_defaults(
    config: dict[str, Any],
    *,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Legacy-shaped remote defaults synthesized from per-pipeline config."""
    user = _user_overlay_for_config(config, user_config=user_config)
    remote: dict[str, Any] = {}
    if rerank_nick := pruning_stage_model_nick(config, "rerank", user_config=user):
        remote["reranking_model_nick"] = rerank_nick
    if llm_nick := pruning_stage_model_nick(config, "llm", user_config=user):
        remote["llm_model_nick"] = llm_nick
    return remote


def _remote_model_entries(config: dict[str, Any], model_kind: str) -> list[dict[str, Any]]:
    entries = _merged_config(config).get("models", {}).get(model_kind, {}).get("remote", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def remote_model_entry(config: dict[str, Any], model_kind: str, model_nick: str) -> dict[str, Any]:
    """Return the ``models.<kind>.remote`` entry for *model_nick*."""
    for entry in _remote_model_entries(config, model_kind):
        if entry.get("nick") == model_nick:
            return entry
    raise ValueError(f"Unknown models.{model_kind}.remote nick: {model_nick!r}")


def key_var_name_for_model_nick(
    config: dict[str, Any],
    model_kind: str,
    model_nick: str,
) -> str:
    """Return ``key_var_name`` for a ``models.<kind>.remote`` entry."""
    entry = remote_model_entry(config, model_kind, model_nick)
    key_var_name = entry.get("key_var_name")
    if not key_var_name:
        raise ValueError(
            f"models.{model_kind}.remote entry {model_nick!r} is missing key_var_name",
        )
    return str(key_var_name)


def _append_pipeline_stage_env_vars(
    config: dict[str, Any],
    add: Callable[[str], None],
) -> None:
    for stage in pruning_pipeline_from_config(config):
        stage_keys = _PIPELINE_STAGE_MODEL_KEYS.get(stage)
        if stage_keys is None:
            continue
        model_kind, nick_key = stage_keys
        if stage not in ("rerank", "llm"):
            continue
        user = load_user_config_overlay()
        if stage == "rerank":
            model_nick = pruning_stage_model_nick(config, "rerank", user_config=user)
        else:
            model_nick = pruning_stage_model_nick(config, "llm", user_config=user)
        if not model_nick:
            raise ValueError(
                f"pruning.{stage}.model.remote.model_nick "
                f"(or defaults.remote.{nick_key}) is required when "
                f"pruning.pipeline includes {stage!r}",
            )
        add(key_var_name_for_model_nick(config, model_kind, model_nick))


def required_proxy_env_var_names(config: dict[str, Any]) -> list[str]:
    """Environment variable names required by configured pruning pipeline stages."""
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    _append_pipeline_stage_env_vars(config, add)

    return required


def missing_proxy_env_var_names(config: dict[str, Any]) -> list[str]:
    """Return unset env var names required by the configured pruning pipeline."""
    load_proxy_env()
    return [name for name in required_proxy_env_var_names(config) if not os.environ.get(name)]


def format_proxy_env_help(missing: list[str]) -> str:
    """Human-readable guidance when pruning pipeline API keys are unset."""
    vars_block = "\n".join(f"\t{name}" for name in missing)
    env_locations = "\n".join(f"\t{p}" for p in (CWD_ENV_PATH, USER_ENV_PATH))
    return (
        f"Required environment variable(s) not set:\n{vars_block}\n"
        f"Export them in the shell or define them in\n{env_locations}\n"
        "\n"
        "To run without API keys, use BM25-only pruning via upstream CLI flags:\n"
        "\tcyt proxy --upstream URL --upstream-kind anthropic|openai "
        "(aliases: claude/claude-code, codex)\n"
        "\n"
        "Or configure pruning and keys interactively:\n"
        "\tcyt setup"
    )


def require_proxy_env(config: dict[str, Any]) -> None:
    """Ensure pruning pipeline API keys are set after loading .env fallbacks."""
    if missing := missing_proxy_env_var_names(config):
        raise RuntimeError(format_proxy_env_help(missing))


def _stage_minimum_tools(
    config: dict[str, Any] | None,
    stage: Literal["rerank", "llm"],
    *,
    user_config: dict[str, Any] | None = None,
) -> int:
    """Resolve stage threshold from shared and legacy config paths."""
    cfg = config or load_config()
    merged = _merged_config(cfg)
    user = _user_overlay_for_config(cfg, user_config=user_config)
    model_kind = "rerankers" if stage == "rerank" else "llm"

    shared = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", "policy", "minimum_tools"),
        legacy_keys=("models", model_kind, "minimum_tools"),
    )
    if shared is not None:
        return int(cast(int | str, shared))

    stage_specific = _resolve_user_then_merged(
        merged,
        user,
        new_keys=("pruning", "policy", stage, "minimum_tools"),
        legacy_keys=(),
    )
    if stage_specific is not None:
        return int(cast(int | str, stage_specific))

    return DEFAULT_MIN_TOOLS_PRUNING


def llm_minimum_tools(
    config: dict[str, Any] | None = None,
    *,
    user_config: dict[str, Any] | None = None,
) -> int:
    """Resolve LLM stage threshold from ``pruning.policy.minimum_tools`` and legacy paths."""
    return _stage_minimum_tools(config, "llm", user_config=user_config)


def reranker_minimum_tools(
    config: dict[str, Any] | None = None,
    *,
    user_config: dict[str, Any] | None = None,
) -> int:
    """Resolve rerank threshold from ``pruning.policy.minimum_tools`` and legacy paths."""
    return _stage_minimum_tools(config, "rerank", user_config=user_config)


def litellm_model_name(entry: dict[str, Any]) -> str:
    """Build the LiteLLM model string for a ``models.<kind>.remote`` entry."""
    provider = entry.get("provider")
    full_model_name = entry.get("name")
    if not provider or not full_model_name:
        nick = entry.get("nick", "<unknown>")
        raise ValueError(
            f"models remote entry {nick!r} is missing provider or name for LiteLLM",
        )
    return f"{provider}/{full_model_name}"


def model_responses_api(entry: dict[str, Any]) -> bool:
    """True when the model entry should call LiteLLM's Responses API (``/v1/responses``)."""
    return bool(entry.get("responses_api", False))


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
            if model_type == "remote":
                load_proxy_env()
                key_var_name = entry.get("key_var_name")
                api_key_value = os.environ.get(key_var_name) if key_var_name else None
                base_url = entry.get("base_url")
                if entry.get("provider"):
                    return litellm_model_name(entry), api_key_value, base_url
                raise ValueError(
                    f"Unknown remote provider for nick: {model_nick}, kind: {model_kind}, type: {model_type} in LiteLLM format",
                )
            full_model_name = entry.get("name")
            return f"{full_model_name}", None, None
    raise ValueError(f"Unknown model nick: {model_nick}, kind: {model_kind}, type: {model_type}")
