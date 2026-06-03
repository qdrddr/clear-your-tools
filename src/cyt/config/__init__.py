"""Centralised configuration loader."""

from __future__ import annotations

import copy
import importlib.resources
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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
DEFAULT_MIN_TOOLS_LLM_PRUNINER: int = 50
DEFAULT_MIN_TOOLS_RERANKER_PRUNINER: int = 10
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
        "llm": {
            "minimum_tools": DEFAULT_MIN_TOOLS_LLM_PRUNINER,
        },
        "rerankers": {
            "minimum_tools": DEFAULT_MIN_TOOLS_RERANKER_PRUNINER,
        },
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
        model_key = "rerankers" if skip["stage"] == "rerank" else "llm"
        logger.warning(
            "Pruning stage %r skipped: %d tools below models.%s.minimum_tools %d",
            skip["stage"],
            skip["tool_count"],
            model_key,
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


def _bm25_settings(config: dict[str, Any]) -> dict[str, Any]:
    bm25 = _merged_config(config).get("models", {}).get("bm25", {})
    return bm25 if isinstance(bm25, dict) else {}


def bm25_index_dir(config: dict[str, Any] | None = None) -> Path:
    path = _bm25_settings(config or load_config()).get("index_dir", DEFAULT_BM25_INDEX_DIR)
    return Path(str(path)).expanduser()


def bm25_mmap_enabled(config: dict[str, Any] | None = None) -> bool:
    return bool(_bm25_settings(config or load_config()).get("mmap", True))


def bm25_stem_language(config: dict[str, Any] | None = None) -> str:
    value = _bm25_settings(config or load_config()).get("stem_language", DEFAULT_BM25_STEM_LANGUAGE)
    return str(value)


def bm25_stopwords(config: dict[str, Any] | None = None) -> str:
    value = _bm25_settings(config or load_config()).get("stopwords", DEFAULT_BM25_STOPWORDS)
    return str(value)


_PIPELINE_STAGE_MODEL_KEYS: dict[str, tuple[str, str]] = {
    "rerank": ("rerankers", "reranking_model_nick"),
    "llm": ("llm", "llm_model_nick"),
}


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


def _remote_pruning_stage_configured(
    user_config: dict[str, Any],
    stage: str,
    remote_defaults: dict[str, Any],
) -> bool:
    stage_keys = _PIPELINE_STAGE_MODEL_KEYS.get(stage)
    if stage_keys is None:
        return False
    model_kind, nick_key = stage_keys
    model_nick = remote_defaults.get(nick_key)
    if not model_nick:
        return False
    return _remote_model_configured(user_config, model_kind=model_kind, model_nick=str(model_nick))


def remote_pruning_pipeline_configured(user_config: dict[str, Any]) -> bool:
    """True when ``config.yaml`` explicitly configures a remote rerank/llm pruning stage."""
    pipeline = _user_pruning_pipeline(user_config)
    if not pipeline:
        return False
    remote_defaults = _user_remote_defaults(user_config)
    return any(
        _remote_pruning_stage_configured(user_config, stage, remote_defaults) for stage in pipeline
    )


def _remote_defaults(config: dict[str, Any]) -> dict[str, Any]:
    remote = _merged_config(config).get("defaults", {}).get("remote", {})
    return remote if isinstance(remote, dict) else {}


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
    remote_defaults: dict[str, Any],
    add: Callable[[str], None],
) -> None:
    for stage in pruning_pipeline_from_config(config):
        stage_keys = _PIPELINE_STAGE_MODEL_KEYS.get(stage)
        if stage_keys is None:
            continue
        model_kind, nick_key = stage_keys
        model_nick = remote_defaults.get(nick_key)
        if not model_nick:
            raise ValueError(
                f"defaults.remote.{nick_key} is required when pruning.pipeline includes {stage!r}",
            )
        add(key_var_name_for_model_nick(config, model_kind, str(model_nick)))


def required_proxy_env_var_names(config: dict[str, Any]) -> list[str]:
    """Environment variable names required by configured pruning pipeline stages."""
    remote_defaults = _remote_defaults(config)
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    _append_pipeline_stage_env_vars(config, remote_defaults, add)

    return required


def require_proxy_env(config: dict[str, Any]) -> None:
    """Ensure pruning pipeline API keys are set after loading .env fallbacks."""
    load_proxy_env()
    if missing := [
        name for name in required_proxy_env_var_names(config) if not os.environ.get(name)
    ]:
        env_locations = " or ".join(str(p) for p in (CWD_ENV_PATH, USER_ENV_PATH))
        raise RuntimeError(
            "Required environment variable(s) not set: "
            f"{', '.join(missing)}. Export them in the shell or define them in {env_locations}.",
        )


def llm_minimum_tools(config: dict[str, Any] | None = None) -> int:
    return int(_merged_config(config or load_config())["models"]["llm"]["minimum_tools"])


def reranker_minimum_tools(config: dict[str, Any] | None = None) -> int:
    return int(_merged_config(config or load_config())["models"]["rerankers"]["minimum_tools"])


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
                load_proxy_env()
                key_var_name = entry.get("key_var_name")
                api_key_value = os.environ.get(key_var_name) if key_var_name else None
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
