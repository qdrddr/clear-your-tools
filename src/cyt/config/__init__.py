"""Centralised configuration loader."""

from __future__ import annotations

import copy
import functools
import importlib.resources
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from cyt_core.types.policies import PolicyContext

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# LiteLLM loads ``~/.env`` on import when ``LITELLM_MODE=DEV`` (the default).
# CYT manages env files explicitly via :func:`load_proxy_env`.
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

BUNDLED_DEFAULTS_NAME = "defaults.yaml"
USER_ENV_PATH = Path("~/.config/cyt/.env").expanduser()


def cwd_env_path() -> Path:
    """Return ``./.env`` for the current working directory (not import-time cwd)."""
    return Path.cwd() / ".env"


CWD_ENV_PATH = cwd_env_path()

_proxy_env_loaded = False


def load_proxy_env() -> None:
    """Load API keys from ``./.env``, then ``~/.config/cyt/.env``.

    Variables already set in the process environment are never overwritten.
    """
    global _proxy_env_loaded
    if _proxy_env_loaded:
        return
    for path in (cwd_env_path(), USER_ENV_PATH):
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)
    _proxy_env_loaded = True


_PROCESS_ENV_BEFORE_DOTENV: dict[str, str] = dict(os.environ)


def process_env_before_dotenv() -> dict[str, str]:
    """Process environment before ``./.env`` and ``~/.config/cyt/.env`` were loaded."""
    return _PROCESS_ENV_BEFORE_DOTENV


load_proxy_env()

# Path and validation constants (not config defaults — those live in defaults.yaml).
DEFAULT_USER_CONFIG_PATH: Path = Path("~/.config/cyt/config.yaml")
CWD_CONFIG_NAME: str = "config.yaml"
ToolPolicy = Literal[
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
]
POLICY_CHOICES: tuple[ToolPolicy, ...] = (
    "always_include",
    "prune_optional",
    "prune_all",
    "prune_optional_descriptions",
    "prune_all_descriptions",
)
VALID_TOOL_POLICIES: frozenset[str] = frozenset(POLICY_CHOICES)
VALID_TOOLS_HOOK_SOURCES: frozenset[str] = frozenset(
    {"executor", "definitions", "mcpc", "cloudflare", "cyt_mcp"},
)
VALID_PRUNING_STAGES: frozenset[str] = frozenset({"rerank", "llm", "bm25"})
ToolsInjectVia = Literal["proxy", "hook"]
ToolsHookSource = Literal["executor", "definitions", "mcpc", "cloudflare", "cyt_mcp"]


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *overlay* over *base* without mutating either."""
    result: dict[str, Any] = {}
    for key in {*base, *overlay}:
        if (
            key in base
            and key in overlay
            and isinstance(base[key], dict)
            and isinstance(overlay[key], dict)
        ):
            result[key] = deep_merge(base[key], overlay[key])
        elif key in overlay:
            result[key] = overlay[key]
        else:
            result[key] = base[key]
    return result


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    return loaded if isinstance(loaded, dict) else {}


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


def _strip_permissions_blocks(config: dict[str, Any]) -> None:
    """Remove permission policy from a config dict (runtime defaults must not carry it)."""
    for key in ("skills", "mcp"):
        block = config.get(key)
        if isinstance(block, dict):
            block.pop("permissions", None)
    agents = config.get("agents")
    if not isinstance(agents, dict):
        return
    for agent_block in agents.values():
        if not isinstance(agent_block, dict):
            continue
        for key in ("skills", "mcp"):
            sub = agent_block.get(key)
            if isinstance(sub, dict):
                sub.pop("permissions", None)


@functools.cache
def _bundled_defaults() -> dict[str, Any]:
    bundled = copy.deepcopy(_load_bundled_defaults_yaml())
    _strip_permissions_blocks(bundled)
    return bundled


def clear_bundled_defaults_cache() -> None:
    """Invalidate cached bundled defaults (for tests monkeypatching yaml load)."""
    _bundled_defaults.cache_clear()


def _bundled_get(*keys: str) -> Any:
    """Read a value from bundled defaults (returns ``None`` when the path is absent)."""
    node: Any = _bundled_defaults()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _default_at(*keys: str) -> Any:
    """Read a required value from ``defaults.yaml``."""
    node: Any = _bundled_defaults()
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"defaults.yaml missing key path: {'.'.join(keys)}")
        node = node[key]
    return node


def _merged_at(config: dict[str, Any], *keys: str) -> Any:
    """Read a required value from merged config (bundled defaults + overlay)."""
    return _require_nested(_merged_config(config), *keys)


def _require_nested(node: Any, *keys: str) -> Any:
    """Read a required nested key path from an already-resolved mapping."""
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(f"config missing key path: {'.'.join(keys)}")
        node = node[key]
    return node


def _bundled_dict(*keys: str) -> dict[str, Any]:
    value = _default_at(*keys)
    return dict(value) if isinstance(value, dict) else {}


def _bundled_list(*keys: str) -> list[Any]:
    value = _bundled_get(*keys)
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    return []


def inject_via_agents() -> dict[str, str]:
    """Default per-agent inject_via map from bundled defaults."""
    raw = _default_at("pruning", "inject_via")
    if not isinstance(raw, dict):
        return {}
    return {str(agent): str(mode) for agent, mode in raw.items()}


def upstream_url_defaults() -> dict[str, str]:
    """Default upstream URLs keyed by endpoint/kind from bundled network config."""
    result: dict[str, str] = {}
    reverse = _default_at("network", "proxy", "reverse")
    if not isinstance(reverse, dict):
        return result
    upstreams = reverse.get("upstreams")
    if not isinstance(upstreams, list):
        return result
    for item in upstreams:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not url:
            continue
        for key in ("kind", "endpoint"):
            label = item.get(key)
            if label:
                result[str(label)] = str(url)
    return result


def default_reverse_port() -> int:
    """Default reverse proxy listen port from bundled defaults."""
    return int(_default_at("network", "proxy", "reverse", "port"))


def _resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return *config* when provided (including ``{}``); otherwise load from disk."""
    return load_config() if config is None else config


def _merged_config(config: dict[str, Any]) -> dict[str, Any]:
    """Layer *config* over bundled defaults for partial config dicts."""
    return deep_merge(_bundled_defaults(), config)


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


def _bundled_pruning_per_tool_overlay(bundled: dict[str, Any]) -> dict[str, Any] | None:
    pruning = bundled.get("pruning")
    if not isinstance(pruning, dict):
        return None
    tools = pruning.get("tools")
    if isinstance(tools, dict):
        policy = tools.get("policy")
        if isinstance(policy, dict) and "per_tool" in policy:
            return {"tools": {"policy": {"per_tool": copy.deepcopy(policy["per_tool"])}}}
    return None


def _bundled_network_reverse_overlay(bundled: dict[str, Any]) -> dict[str, Any] | None:
    network = bundled.get("network")
    if not isinstance(network, dict):
        return None
    proxy = network.get("proxy")
    if not isinstance(proxy, dict):
        return None
    reverse = proxy.get("reverse")
    if not isinstance(reverse, dict):
        return None
    reverse_overlay: dict[str, Any] = {}
    for key in ("inject_into_user_message", "debug_log_dir", "debug_log_max_body_bytes", "http2"):
        if key in reverse:
            reverse_overlay[key] = copy.deepcopy(reverse[key])
    return reverse_overlay or None


def _copy_permissions_block(source: dict[str, Any], key: str) -> dict[str, Any] | None:
    block = source.get(key)
    if not isinstance(block, dict):
        return None
    permissions = block.get("permissions")
    if not isinstance(permissions, dict):
        return None
    return {key: {"permissions": copy.deepcopy(permissions)}}


def _bundled_agent_permissions_overlay(bundled: dict[str, Any]) -> dict[str, Any]:
    agents = bundled.get("agents")
    if not isinstance(agents, dict):
        return {}
    agents_overlay: dict[str, Any] = {}
    for agent_name, agent_block in agents.items():
        if not isinstance(agent_block, dict):
            continue
        copied = _copy_permissions_block(agent_block, "skills")
        if copied is not None:
            agents_overlay.setdefault(agent_name, {}).update(copied)
        copied_mcp = _copy_permissions_block(agent_block, "mcp")
        if copied_mcp is not None:
            agents_overlay.setdefault(agent_name, {}).update(copied_mcp)
    return agents_overlay


def _bundled_permissions_overlay(bundled: dict[str, Any]) -> dict[str, Any] | None:
    result: dict[str, Any] = {}

    skills_overlay = (
        _copy_permissions_block(bundled, "skills") if isinstance(bundled, dict) else None
    )
    if skills_overlay is not None:
        result.update(skills_overlay)

    mcp_overlay = _copy_permissions_block(bundled, "mcp") if isinstance(bundled, dict) else None
    if mcp_overlay is not None:
        result.update(mcp_overlay)

    agents_overlay = _bundled_agent_permissions_overlay(bundled)
    if agents_overlay:
        result["agents"] = agents_overlay

    return result or None


def bundled_user_config_sections() -> dict[str, Any]:
    """Packaged-default sections that belong in the on-disk user config file."""
    bundled = _load_bundled_defaults_yaml()
    result: dict[str, Any] = {}

    if pruning_overlay := _bundled_pruning_per_tool_overlay(bundled):
        result["pruning"] = pruning_overlay

    if reverse_overlay := _bundled_network_reverse_overlay(bundled):
        result.setdefault("network", {}).setdefault("proxy", {})["reverse"] = reverse_overlay

    if permissions_overlay := _bundled_permissions_overlay(bundled):
        result = deep_merge(result, permissions_overlay)

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
    bundled = _load_bundled_defaults_yaml()
    result: dict[str, Any] = {}
    defaults_section = bundled.get("defaults")
    if isinstance(defaults_section, dict):
        result["defaults"] = copy.deepcopy(defaults_section)
    result = deep_merge(result, bundled_user_config_sections())
    reverse = _default_at("network", "proxy", "reverse")
    if isinstance(reverse, dict):
        reverse_seed: dict[str, Any] = {}
        if "port" in reverse:
            reverse_seed["port"] = reverse["port"]
        if "upstreams" in reverse:
            reverse_seed["upstreams"] = copy.deepcopy(reverse["upstreams"])
        if "endpoints" in reverse:
            reverse_seed["endpoints"] = copy.deepcopy(reverse["endpoints"])
        if reverse_seed:
            result = deep_merge(result, {"network": {"proxy": {"reverse": reverse_seed}}})
    stats_path = _bundled_get("stats", "database", "path")
    if stats_path is not None:
        result = deep_merge(result, {"stats": {"database": {"path": stats_path}}})
    return result


def _write_default_user_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.dump(
        _default_user_config_dict(),
        default_flow_style=False,
        sort_keys=False,
    )
    config_path.write_text(content, encoding="utf-8")


def _config_fingerprint(data: dict[str, Any]) -> str:
    """Stable semantic fingerprint for comparing config dicts."""
    return json.dumps(data, sort_keys=True, default=str)


def _build_merged_user_config(
    path: Path,
    overlay: dict[str, Any],
    *,
    apply_bundled_sections: bool,
) -> dict[str, Any]:
    existing: dict[str, Any] = _load_yaml_dict(path) if path.exists() else {}
    bundled_sections = bundled_user_config_sections() if apply_bundled_sections else {}
    combined_overlay = deep_merge(bundled_sections, overlay) if apply_bundled_sections else overlay
    merged = deep_merge(existing, combined_overlay)
    if apply_bundled_sections:
        _force_deep_assign(merged, bundled_sections)
    return merged


def default_model_nick(provider: str, name: str) -> str:
    """Build a default model nick from provider and LiteLLM model name."""
    raw = f"{provider}-{name}"
    return re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()


def save_user_config(
    path: Path,
    overlay: dict[str, Any],
    *,
    apply_bundled_sections: bool = False,
) -> bool:
    """Deep-merge *overlay* onto an existing user config file and write YAML.

    Returns ``True`` when the file was written, ``False`` when unchanged.
    """
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = _load_yaml_dict(path) if path.exists() else {}
    merged = _build_merged_user_config(
        path,
        overlay,
        apply_bundled_sections=apply_bundled_sections,
    )
    if _config_fingerprint(merged) == _config_fingerprint(existing):
        return False
    content = yaml.dump(merged, default_flow_style=False, sort_keys=False)
    path.write_text(content, encoding="utf-8")
    return True


def resolve_setup_config_path(path: Path | None = None) -> Path:
    """Config path for ``proxy setup`` (defaults to ``~/.config/cyt/config.yaml``)."""
    if path is not None:
        return path.expanduser()
    return DEFAULT_USER_CONFIG_PATH.expanduser()


def load_bundled_defaults_yaml() -> dict[str, Any]:
    """Load packaged ``defaults.yaml`` (public wrapper)."""
    return _load_bundled_defaults_yaml()


def _config_with_bundled_defaults(user_config: dict[str, Any]) -> dict[str, Any]:
    """Layer bundled ``defaults.yaml``, then *user_config*.

    Permission policy is excluded from bundled defaults at runtime; effective
    permissions come from on-disk ``config.yaml`` overlays only.
    """
    merged = deep_merge(_bundled_defaults(), user_config)
    return merged


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


def sync_config_in_place(target: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    """Reload config from disk into *target* so existing references stay current."""
    reloaded = load_config(path)
    target.clear()
    target.update(copy.deepcopy(reloaded))
    return target


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
    default_http2 = _bundled_dict("network", "proxy", "reverse", "http2")
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


def stats_rollup_on_query(config: dict[str, Any]) -> bool:
    return bool(_merged_at(config, "stats", "rollup_on_query"))


def stats_backup_before_rollup(config: dict[str, Any]) -> bool:
    return bool(_merged_at(config, "stats", "backup_before_rollup"))


def pruning_pipeline_from_config(config: dict[str, Any]) -> list[str]:
    merged = _merged_config(config)
    sequence = _resolve_user_then_merged(
        merged,
        config,
        keys=("pruning", "tools", "sequence"),
    )
    if sequence is not None:
        if not isinstance(sequence, list) or not all(isinstance(s, str) for s in sequence):
            raise ValueError("pruning.tools.sequence must be a list of stage names")
        return cast(list[str], sequence)
    sequence = _require_nested(merged, "pruning", "tools", "sequence")
    if not isinstance(sequence, list) or not all(isinstance(s, str) for s in sequence):
        raise ValueError("pruning.tools.sequence must be a list of stage names")
    return list(sequence)


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
            "Pruning stage %r skipped: %d tools below pruning.tools.policy.minimum_tools %d",
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


def _tools(config: dict[str, Any]) -> dict[str, Any]:
    tools = _pruning_section(config).get("tools")
    return tools if isinstance(tools, dict) else {}


def _pipeline_def(config: dict[str, Any], stage: str) -> dict[str, Any]:
    user_canonical = _nested_dict_value(config, "pruning", "tools", "pipelines", stage)
    if isinstance(user_canonical, dict):
        return cast(dict[str, Any], user_canonical)
    merged = _merged_config(config)
    merged_canonical = _nested_dict_value(merged, "pruning", "tools", "pipelines", stage)
    if isinstance(merged_canonical, dict):
        return cast(dict[str, Any], merged_canonical)
    return {}


def _resolve_user_then_merged(
    merged: dict[str, Any],
    user: dict[str, Any],
    *,
    keys: tuple[str, ...],
) -> object | None:
    """Prefer explicit user keys, then merged keys."""
    user_value = _nested_dict_value(user, *keys)
    if user_value is not None:
        return user_value
    return _nested_dict_value(merged, *keys)


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
    """Resolve system tool policy from ``pruning.tools.policy.system_tool``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    policy = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "policy", "system_tool"),
    )
    if policy is None:
        return cast(ToolPolicy, _require_nested(merged, "pruning", "tools", "policy", "system_tool"))
    return cast(ToolPolicy, policy)


def pruning_mcp_tool_policy(
    config: dict[str, Any],
    *,
    user_config: dict[str, Any] | None = None,
) -> ToolPolicy:
    """Resolve MCP tool policy from ``pruning.tools.policy.mcp_tool``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    policy = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "policy", "mcp_tool"),
    )
    if policy is None:
        return cast(ToolPolicy, _require_nested(merged, "pruning", "tools", "policy", "mcp_tool"))
    return cast(ToolPolicy, policy)


def _pruning_stage_policy_section(
    config: dict[str, Any],
    stage: str,
) -> dict[str, Any]:
    stage_cfg = _pipeline_def(config, stage)
    policy = stage_cfg.get("policy")
    return policy if isinstance(policy, dict) else {}


def _pruning_stage_per_tool(
    config: dict[str, Any],
    stage: str,
) -> dict[str, ToolPolicy]:
    stage_cfg = _pipeline_def(config, stage)
    per_tool = stage_cfg.get("per_tool")
    if not isinstance(per_tool, dict):
        return {}
    out: dict[str, ToolPolicy] = {}
    for tool_id, policy in per_tool.items():
        if isinstance(policy, str) and policy in VALID_TOOL_POLICIES:
            out[str(tool_id)] = cast(ToolPolicy, policy)
    return out


def _per_tool_policy(pruning: dict[str, Any], tool_id: str) -> ToolPolicy | None:
    tools = pruning.get("tools")
    if isinstance(tools, dict):
        policy_section = tools.get("policy")
        if isinstance(policy_section, dict):
            per_tool = policy_section.get("per_tool")
            if isinstance(per_tool, dict) and tool_id in per_tool:
                policy = per_tool[tool_id]
                if isinstance(policy, str) and policy in VALID_TOOL_POLICIES:
                    return cast(ToolPolicy, policy)
    return None


def _category_policy_from_section(section: dict[str, Any], tool_id: str) -> ToolPolicy | None:
    key = "mcp_tool" if is_non_system_tool_id(tool_id) else "system_tool"
    policy = section.get(key)
    if isinstance(policy, str) and policy in VALID_TOOL_POLICIES:
        return cast(ToolPolicy, policy)
    return None


def effective_output_policy(
    config: dict[str, Any],
    tool_id: str,
    *,
    terminal_stage: str | None = None,
    user_config: dict[str, Any] | None = None,
) -> ToolPolicy:
    """Resolve output policy for a tool (main + per-pipeline overrides)."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    pruning = merged.get("pruning")
    pruning_dict = pruning if isinstance(pruning, dict) else {}

    if terminal_stage:
        if policy := _pruning_stage_per_tool(merged, terminal_stage).get(tool_id):
            return policy
        if policy := _per_tool_policy(pruning_dict, tool_id):
            return policy
        if policy := _category_policy_from_section(
            _pruning_stage_policy_section(merged, terminal_stage),
            tool_id,
        ):
            return policy

    if policy := _per_tool_policy(pruning_dict, tool_id):
        return policy

    if is_non_system_tool_id(tool_id):
        return pruning_mcp_tool_policy(merged, user_config=user)
    return pruning_system_tool_policy(merged, user_config=user)


def is_non_system_tool_id(tool_id: str) -> bool:
    return tool_id.startswith("mcp__")


def _apply_stage_policy_to_context(
    ctx: PolicyContext,
    config: dict[str, Any],
    terminal_stage: str,
) -> None:
    stage_policy = _pruning_stage_policy_section(config, terminal_stage)
    if sys := stage_policy.get("system_tool"):
        if isinstance(sys, str) and sys in VALID_TOOL_POLICIES:
            ctx.system_policy = cast(ToolPolicy, sys)
    if mcp_pol := stage_policy.get("mcp_tool"):
        if isinstance(mcp_pol, str) and mcp_pol in VALID_TOOL_POLICIES:
            ctx.mcp_policy = cast(ToolPolicy, mcp_pol)
    stage_per_tool = _pruning_stage_per_tool(config, terminal_stage)
    if stage_per_tool:
        merged_per_tool = dict(ctx.per_tool)
        merged_per_tool.update(stage_per_tool)
        ctx.per_tool = merged_per_tool


def output_policy_context_for_terminal_stage(
    config: dict[str, Any] | None = None,
    *,
    terminal_stage: str | None = None,
    system: ToolPolicy | None = None,
    mcp: ToolPolicy | None = None,
    per_tool: dict[str, ToolPolicy] | None = None,
) -> PolicyContext:
    """Build output policy context (may include ``*_descriptions`` policies)."""
    from cyt.indexer.policies import policy_context_from_values

    if config is None:
        config = load_config()
    ctx = policy_context_from_values(config)

    if terminal_stage:
        _apply_stage_policy_to_context(ctx, config, terminal_stage)

    if system is not None:
        ctx.system_policy = system
    if mcp is not None:
        ctx.mcp_policy = mcp
    if per_tool:
        merged_per_tool = dict(ctx.per_tool)
        merged_per_tool.update(per_tool)
        ctx.per_tool = merged_per_tool
    return ctx


def scoring_policy_context(ctx: PolicyContext) -> PolicyContext:
    """Map description policies to base scoring policies for partition/pipeline."""
    from cyt.indexer.policies import scoring_policy_context as sdk_scoring_policy_context

    return sdk_scoring_policy_context(ctx)


def pruning_stage_model_nick(
    config: dict[str, Any],
    stage: Literal["rerank", "llm"],
    *,
    user_config: dict[str, Any] | None = None,
) -> str | None:
    """Resolve stage model nick from ``pruning.tools.pipelines.<stage>.model_nick``."""
    merged = _merged_config(config)
    user = _user_overlay_for_config(config, user_config=user_config)
    nick = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "pipelines", stage, "model_nick"),
    )
    return str(nick) if nick is not None else None


def _bm25_index_dir_resolved(
    merged: dict[str, Any],
    user: dict[str, Any],
) -> str:
    index_dir = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "pipelines", "bm25", "index_dir"),
    )
    if index_dir is not None:
        return str(index_dir)
    cache = merged.get("cache")
    if isinstance(cache, dict) and bool(cache.get("enabled")):
        bm25_dir = cache.get("bm25_dir")
        if bm25_dir is not None:
            return str(bm25_dir)
    return str(_default_at("pruning", "tools", "pipelines", "bm25", "index_dir"))


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
    cfg = _resolve_config(config)
    user = load_user_config_overlay() if config is None else None
    path = _bm25_settings(cfg, user_config=user)["index_dir"]
    return Path(str(path)).expanduser()


def bm25_mmap_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "models", "bm25", "mmap"))


def bm25_stem_language(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    return str(_merged_at(cfg, "models", "bm25", "stem_language"))


def bm25_stopwords(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    return str(_merged_at(cfg, "models", "bm25", "stopwords"))


def _bm25_pruning_settings(config: dict[str, Any]) -> dict[str, Any]:
    return _pipeline_def(config, "bm25")


def bm25_score_tool(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "pruning", "tools", "pipelines", "bm25", "score_tool"))


def bm25_prune_enums(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "pruning", "tools", "pipelines", "bm25", "prune_enums"))


def bm25_score_tool_enum(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "pruning", "tools", "pipelines", "bm25", "score_tool_enum"))


def bm25_score_skills(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "pruning", "tools", "pipelines", "bm25", "score_skills"))


def _rerank_pruning_settings(config: dict[str, Any]) -> dict[str, Any]:
    return _pipeline_def(config, "rerank")


def rerank_score_skills(config: dict[str, Any] | None = None) -> float:
    from cyt.common.runtime_constants import RERANK_SCORE

    cfg = _resolve_config(config)
    value = _rerank_pruning_settings(cfg).get("score_skills", RERANK_SCORE)
    return float(value)


def _llm_pruning_settings(config: dict[str, Any]) -> dict[str, Any]:
    return _pipeline_def(config, "llm")


def llm_score(config: dict[str, Any] | None = None) -> int:
    from cyt.common.runtime_constants import LLM_SCORE

    cfg = _resolve_config(config)
    value = _llm_pruning_settings(cfg).get("score_tool", LLM_SCORE)
    return int(value)


def llm_enum_score(config: dict[str, Any] | None = None) -> int:
    from cyt.common.runtime_constants import LLM_ENUM_SCORE

    cfg = _resolve_config(config)
    value = _llm_pruning_settings(cfg).get("score_tool_enum", LLM_ENUM_SCORE)
    return int(value)


def _skills_settings(config: dict[str, Any]) -> dict[str, Any]:
    skills = config.get("skills")
    return skills if isinstance(skills, dict) else {}


def _skills_hook_settings(config: dict[str, Any]) -> dict[str, Any]:
    skills = _skills_settings(config)
    hook = skills.get("hook")
    return hook if isinstance(hook, dict) else {}


def _skills_proxy_settings(config: dict[str, Any]) -> dict[str, Any]:
    skills = _skills_settings(config)
    proxy = skills.get("proxy")
    return proxy if isinstance(proxy, dict) else {}


def _tools_hook_settings(config: dict[str, Any]) -> dict[str, Any]:
    tools = _tools(_merged_config(config))
    hook = tools.get("hook")
    return hook if isinstance(hook, dict) else {}


def inject_via_map_for_mode(mode: str) -> dict[str, str]:
    """Build per-agent inject_via map from a wizard choice (hook | proxy)."""
    normalized = mode.strip().lower()
    if normalized == "hook":
        return dict.fromkeys(inject_via_agents(), "hook")
    return dict(inject_via_agents())


def _inject_via_map(config: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = _resolve_config(config)
    merged = _merged_config(cfg)
    pruning = merged.get("pruning")
    if not isinstance(pruning, dict):
        return dict(inject_via_agents())
    raw = pruning.get("inject_via")
    if isinstance(raw, dict):
        result = dict(inject_via_agents())
        for agent, value in raw.items():
            agent_key = str(agent).strip()
            if agent_key not in inject_via_agents():
                continue
            mode = str(value).strip().lower()
            if mode in {"hook", "proxy"}:
                result[agent_key] = mode
        return result
    return dict(inject_via_agents())


def inject_via_map(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Per-agent inject_via map (cursor/claude/codex)."""
    return _inject_via_map(config)


def _any_agent_uses_hook_tools(config: dict[str, Any]) -> bool:
    return any(
        inject_via_for_agent(config, agent) == "hook" for agent in inject_via_agents()
    )


def inject_via_for_agent(config: dict[str, Any] | None, agent: str) -> ToolsInjectVia:
    """Per-agent injection path (hook or proxy)."""
    cfg = _resolve_config(config)
    mode = _inject_via_map(cfg).get(agent)
    if mode == "hook":
        return "hook"
    if mode == "proxy":
        return "proxy"
    fallback = str(_merged_at(cfg, "pruning", "inject_via_default")).strip().lower()
    return "hook" if fallback == "hook" else "proxy"


def hallucination_gate_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "hallucination_gate", "enabled"))


def verify_only_mode(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return hallucination_gate_enabled(cfg) and not skills_enabled(cfg) and not tools_enabled(cfg)


def verify_session_log_allowed(
    config: dict[str, Any] | None,
    agent: str,
    inject_path: ToolsInjectVia,
) -> bool:
    return verify_only_mode(config) and inject_via_for_agent(config, agent) == inject_path


def any_agent_needs_proxy(config: dict[str, Any] | None = None) -> bool:
    return any(mode == "proxy" for mode in _inject_via_map(config).values())


def needs_cyt_mcp_catalog(config: dict[str, Any] | None = None, agent: str | None = None) -> bool:
    cfg = _resolve_config(config)
    resolved_agent = agent or tools_hook_cyt_mcp_agent(cfg)
    if tools_enabled(cfg) and inject_via_for_agent(cfg, resolved_agent) == "hook":
        return "cyt_mcp" in tools_hook_sources(cfg)
    if verify_only_mode(cfg) and "cyt_mcp" in tools_hook_sources(cfg):
        return True
    return False


def inject_via(config: dict[str, Any] | None = None) -> ToolsInjectVia:
    """Legacy helper: inject path for the cyt_mcp hook agent."""
    cfg = _resolve_config(config)
    return inject_via_for_agent(cfg, tools_hook_cyt_mcp_agent(cfg))


def tools_inject_via(
    config: dict[str, Any] | None = None,
    *,
    agent: str | None = None,
) -> ToolsInjectVia:
    cfg = _resolve_config(config)
    resolved = agent or tools_hook_cyt_mcp_agent(cfg)
    return inject_via_for_agent(cfg, resolved)


def skills_inject_via(config: dict[str, Any] | None = None, *, agent: str | None = None) -> str:
    return tools_inject_via(config, agent=agent)


def tools_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "pruning", "tools", "enabled"))


def inject_into_user_message(
    config: dict[str, Any] | None = None,
    *,
    agent: str | None = None,
) -> bool:
    """When true (proxy only), inject pruned MCP tools and skills into the latest user turn."""
    cfg = _resolve_config(config)
    resolved = agent or tools_hook_cyt_mcp_agent(cfg)
    if inject_via_for_agent(cfg, resolved) != "proxy":
        return False
    reverse_cfg = reverse_proxy_cfg(_merged_config(cfg)["network"]["proxy"])
    value = reverse_cfg.get("inject_into_user_message", _merged_at(cfg, "network", "proxy", "reverse", "inject_into_user_message"))
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_tools_hook_source(value: str) -> ToolsHookSource | None:
    mode = str(value).strip().lower()
    if mode == "definitions":
        return "definitions"
    if mode == "mcpc":
        return "mcpc"
    if mode in {"cyt_mcp", "cyt-mcp"}:
        return "cyt_mcp"
    if mode in {"executor", "client"}:
        return "executor"
    if mode == "cloudflare":
        return "cloudflare"
    return None


def tools_hook_sources(config: dict[str, Any] | None = None) -> tuple[ToolsHookSource, ...]:
    """Normalized ``pruning.tools.hook.tools_from`` list (scalar or YAML array)."""
    cfg = _resolve_config(config)
    value = _merged_at(cfg, "pruning", "tools", "hook", "tools_from")
    raw_items: list[Any]
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raise ValueError("pruning.tools.hook.tools_from must be a string or list of strings")

    seen: set[ToolsHookSource] = set()
    normalized: list[ToolsHookSource] = []
    for item in raw_items:
        source = _normalize_tools_hook_source(str(item))
        if source is None or source in seen:
            continue
        seen.add(source)
        normalized.append(source)
    if not normalized:
        if any(str(item).strip() for item in raw_items):
            return ("executor",)
        default_sources = _merged_at(cfg, "pruning", "tools", "hook", "tools_from")
        if isinstance(default_sources, str):
            default_sources = [default_sources]
        return cast(
            tuple[ToolsHookSource, ...],
            tuple(
                source
                for item in default_sources
                if (source := _normalize_tools_hook_source(str(item))) is not None
            ),
        )
    return tuple(normalized)


def tools_hook_tools_from(config: dict[str, Any] | None = None) -> ToolsHookSource:
    """Legacy: first configured hook tool source."""
    sources = tools_hook_sources(config)
    return sources[0]


def uses_definitions_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return (
        tools_enabled(cfg)
        and _any_agent_uses_hook_tools(cfg)
        and "definitions" in tools_hook_sources(cfg)
    )


def tools_hook_executor_url(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    value = _merged_at(cfg, "pruning", "tools", "hook", "executor_url")
    return str(value).strip().rstrip("/")


def tools_hook_executor_token_var(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(_merged_at(cfg, "pruning", "tools", "hook", "executor_token_var")).strip()
    return text or str(_default_at("pruning", "tools", "hook", "executor_token_var"))


def tools_hook_cloudflare_url(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    value = _merged_at(cfg, "pruning", "tools", "hook", "cloudflare_url")
    return str(value).strip().rstrip("/")


def tools_hook_cloudflare_access_client_id_var(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(_merged_at(cfg, "pruning", "tools", "hook", "cloudflare_access_client_id_var")).strip()
    return text or str(_default_at("pruning", "tools", "hook", "cloudflare_access_client_id_var"))


def tools_hook_cloudflare_access_client_secret_var(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(
        _merged_at(cfg, "pruning", "tools", "hook", "cloudflare_access_client_secret_var"),
    ).strip()
    return text or str(_default_at("pruning", "tools", "hook", "cloudflare_access_client_secret_var"))


def tools_hook_cloudflare_configured(config: dict[str, Any] | None = None) -> bool:
    return bool(tools_hook_cloudflare_url(config))


def tools_hook_cloudflare_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _resolve_config(config)
    hook = _tools_hook_settings(_merged_config(cfg))
    cache = hook.get("cloudflare_cache")
    if not isinstance(cache, dict):
        cache = {}
    defaults = _bundled_dict("pruning", "tools", "hook", "cloudflare_cache")
    return deep_merge(defaults, cache)


def uses_cloudflare_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return (
        tools_enabled(cfg)
        and _any_agent_uses_hook_tools(cfg)
        and "cloudflare" in tools_hook_sources(cfg)
    )


def tools_hook_executor_configured(config: dict[str, Any] | None = None) -> bool:
    return bool(tools_hook_executor_url(config))


def _tools_hook_mcpc_settings(config: dict[str, Any]) -> dict[str, Any]:
    hook = _tools_hook_settings(config)
    mcpc = hook.get("mcpc")
    if isinstance(mcpc, dict):
        return mcpc
    return {}


def tools_hook_mcpc_executable(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(_merged_at(cfg, "pruning", "tools", "hook", "mcpc", "executable")).strip()
    return text or str(_default_at("pruning", "tools", "hook", "mcpc", "executable"))


def tools_hook_mcpc_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merged ``pruning.tools.hook.mcpc.cache`` refresh intervals."""
    cfg = _resolve_config(config)
    mcpc = _tools_hook_mcpc_settings(_merged_config(cfg))
    cache = mcpc.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    defaults = _bundled_dict("pruning", "tools", "hook", "mcpc", "cache")
    return deep_merge(defaults, cache)


def mcpc_skills_own_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "pruning", "tools", "hook", "mcpc", "skills", "own", "enabled"))


def mcpc_skills_in_session_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "pruning", "tools", "hook", "mcpc", "skills", "in_session", "enabled"))


def mcpc_resources_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "pruning", "tools", "hook", "mcpc", "resources", "enabled"))


def mcpc_resources_mime_types(config: dict[str, Any] | None = None) -> list[str]:
    cfg = _resolve_config(config)
    mime_types = _merged_at(cfg, "pruning", "tools", "hook", "mcpc", "resources", "mimeType")
    if not isinstance(mime_types, list):
        raise ValueError("pruning.tools.hook.mcpc.resources.mimeType must be a list")
    return [str(item).strip() for item in mime_types if str(item).strip()]


def mcpc_skills_refresh_seconds(config: dict[str, Any] | None = None) -> float:
    cache = tools_hook_mcpc_cache_settings(config)
    value = cache.get("skills_refresh_seconds")
    if value is None:
        return float(_default_at("pruning", "tools", "hook", "mcpc", "cache", "skills_refresh_seconds"))
    return float(value)


def _tools_hook_cyt_mcp_settings(config: dict[str, Any]) -> dict[str, Any]:
    hook = _tools_hook_settings(config)
    cyt_mcp = hook.get("cyt_mcp")
    if isinstance(cyt_mcp, dict):
        return cyt_mcp
    return {}


def tools_hook_cyt_mcp_executable(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(_merged_at(cfg, "pruning", "tools", "hook", "cyt_mcp", "executable")).strip()
    return text or str(_default_at("pruning", "tools", "hook", "cyt_mcp", "executable"))


def tools_hook_cyt_mcp_agent(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    text = str(_merged_at(cfg, "pruning", "tools", "hook", "cyt_mcp", "agent")).strip()
    return text or str(_default_at("pruning", "tools", "hook", "cyt_mcp", "agent"))


def mcp_permissions_overlay(
    config: dict[str, Any] | None = None,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return raw ``mcp.permissions`` block from config (global or agent overlay)."""
    from cyt.permissions.merge import _agent_mcp_permissions, _mcp_permissions_from_config

    cfg = _resolve_config(config)
    if agent is None:
        block = _mcp_permissions_from_config(cfg)
    else:
        block = _agent_mcp_permissions(cfg, agent.strip())
    return {"deny": list(block.deny), "allow": list(block.allow)}


def skills_permissions_overlay(
    config: dict[str, Any] | None = None,
    *,
    agent: str | None = None,
) -> dict[str, Any]:
    """Return raw ``skills.permissions`` block from config (global or agent overlay)."""
    from cyt.permissions.merge import _agent_skills_permissions, _skills_permissions_from_config

    cfg = _resolve_config(config)
    if agent is None:
        block = _skills_permissions_from_config(cfg)
    else:
        block = _agent_skills_permissions(cfg, agent.strip())
    return {"deny": list(block.deny), "allow": list(block.allow)}


def tools_hook_cyt_mcp_catalog_url(config: dict[str, Any] | None = None) -> str:
    """Deprecated: cyt-mcp catalogs are push-registered; always returns empty."""
    del config
    return ""


def tools_hook_cyt_mcp_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _resolve_config(config)
    cyt_mcp = _tools_hook_cyt_mcp_settings(_merged_config(cfg))
    cache = cyt_mcp.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    defaults = _bundled_dict("pruning", "tools", "hook", "cyt_mcp", "cache")
    return deep_merge(defaults, cache)


def connection_health_flapping_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merged ``pruning.tools.hook.connection_health.flapping`` settings."""
    cfg = _resolve_config(config)
    hook = _tools_hook_settings(_merged_config(cfg))
    connection_health = hook.get("connection_health")
    if not isinstance(connection_health, dict):
        connection_health = {}
    flapping = connection_health.get("flapping")
    if not isinstance(flapping, dict):
        flapping = {}
    defaults = _bundled_dict("pruning", "tools", "hook", "connection_health", "flapping")
    return deep_merge(defaults, flapping)


def tools_hook_executor_cache_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merged ``pruning.tools.hook.executor_cache`` refresh intervals."""
    cfg = _resolve_config(config)
    hook = _tools_hook_settings(_merged_config(cfg))
    executor_cache = hook.get("executor_cache")
    if not isinstance(executor_cache, dict):
        executor_cache = {}
    defaults = _bundled_dict("pruning", "tools", "hook", "executor_cache")
    return deep_merge(defaults, executor_cache)


def tools_hook_mcp_definitions_file(config: dict[str, Any] | None = None) -> Path:
    cfg = _resolve_config(config)
    path = _merged_at(cfg, "pruning", "tools", "hook", "mcp_definitions_file")
    return Path(str(path)).expanduser()


def resolved_tools_hook_file(config: dict[str, Any] | None = None) -> Path:
    cfg = _resolve_config(config)
    return tools_hook_mcp_definitions_file(cfg)


def _tools_hook_source_usable(source: ToolsHookSource, config: dict[str, Any]) -> bool:
    if source == "executor":
        return tools_hook_executor_configured(config)
    if source == "mcpc":
        return True
    if source == "cyt_mcp":
        return True
    if source == "cloudflare":
        return tools_hook_cloudflare_configured(config)
    return resolved_tools_hook_file(config).is_file()


def tools_hook_file_missing(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    agent = tools_hook_cyt_mcp_agent(cfg)
    if not tools_enabled(cfg):
        return False
    if inject_via_for_agent(cfg, agent) != "hook":
        return False
    sources = tools_hook_sources(cfg)
    if not sources:
        return True
    return not any(_tools_hook_source_usable(source, cfg) for source in sources)


def required_tools_hook_env_var_names(config: dict[str, Any] | None = None) -> list[str]:
    cfg = _resolve_config(config)
    agent = tools_hook_cyt_mcp_agent(cfg)
    if not tools_enabled(cfg):
        return []
    if inject_via_for_agent(cfg, agent) != "hook":
        return []
    names: list[str] = []
    sources = tools_hook_sources(cfg)
    if "executor" in sources:
        names.append(tools_hook_executor_token_var(cfg))
    if "cloudflare" in sources:
        names.append(tools_hook_cloudflare_access_client_id_var(cfg))
        names.append(tools_hook_cloudflare_access_client_secret_var(cfg))
    return list(dict.fromkeys(names))


def uses_executor_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    agent = tools_hook_cyt_mcp_agent(cfg)
    return (
        tools_enabled(cfg)
        and inject_via_for_agent(cfg, agent) == "hook"
        and "executor" in tools_hook_sources(cfg)
    )


def uses_mcpc_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    agent = tools_hook_cyt_mcp_agent(cfg)
    return (
        tools_enabled(cfg)
        and inject_via_for_agent(cfg, agent) == "hook"
        and "mcpc" in tools_hook_sources(cfg)
    )


def uses_cyt_mcp_tool_catalog(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    agent = tools_hook_cyt_mcp_agent(cfg)
    if (
        tools_enabled(cfg)
        and inject_via_for_agent(cfg, agent) == "hook"
        and "cyt_mcp" in tools_hook_sources(cfg)
    ):
        return True
    return verify_only_mode(cfg) and "cyt_mcp" in tools_hook_sources(cfg)


def launch_needs_proxy(config: dict[str, Any] | None = None, agent: str | None = None) -> bool:
    cfg = _resolve_config(config)
    resolved = agent or "claude"
    return inject_via_for_agent(cfg, resolved) == "proxy"


def skills_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "skills", "enabled"))


def skills_pipeline(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    return str(_merged_at(cfg, "skills", "pipeline"))


def skills_pipeline_uses_llm(config: dict[str, Any] | None = None) -> bool:
    return skills_pipeline(config).strip().lower() == "llm"


def skills_pipeline_uses_rerank(config: dict[str, Any] | None = None) -> bool:
    return skills_pipeline(config).strip().lower() == "rerank"


def skills_pipeline_uses_combined_remote_prune(config: dict[str, Any] | None = None) -> bool:
    pipeline = skills_pipeline(config).strip().lower()
    return pipeline in ("llm", "rerank")


def skills_pipeline_uses_deferred_proxy_inject(config: dict[str, Any] | None = None) -> bool:
    return skills_pipeline_uses_combined_remote_prune(config)


def effective_skills_pipeline(
    config: dict[str, Any],
    eligible_count: int,
) -> str:
    """Resolve configured skills pipeline, substituting bm25 when remote stages cannot run."""
    configured = skills_pipeline(config).strip().lower()
    if configured == "bm25":
        return "bm25"
    if eligible_count < skills_bm25_node_fallback_threshold(config):
        return "bm25"
    if configured in ("rerank", "llm"):
        return configured
    return "bm25"


def skills_catalog_dir(config: dict[str, Any] | None = None) -> str:
    cfg = _resolve_config(config)
    path = _merged_at(cfg, "skills", "catalog_dir")
    return str(Path(str(path)).expanduser())


def _cache_settings(config: dict[str, Any]) -> dict[str, Any]:
    cache = _merged_config(config).get("cache")
    return cache if isinstance(cache, dict) else {}


def cache_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "cache", "enabled"))


def cache_bm25_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = _resolve_config(config)
    path = _merged_at(cfg, "cache", "bm25_dir")
    return Path(str(path)).expanduser()


def cache_skills_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = _resolve_config(config)
    path = _merged_at(cfg, "cache", "skills_dir")
    return Path(str(path)).expanduser()


def cache_tools_dir(config: dict[str, Any] | None = None) -> Path:
    cfg = _resolve_config(config)
    path = _merged_at(cfg, "cache", "tools_dir")
    return Path(str(path)).expanduser()


def cache_memory_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the ``cache.memory`` block for Rust in-memory cache tuning."""
    cache = _cache_settings(_resolve_config(config))
    memory = cache.get("memory")
    return dict(memory) if isinstance(memory, dict) else {}


def skills_max_tokens_per_request(config: dict[str, Any] | None = None) -> int:
    cfg = _resolve_config(config)
    return int(_merged_at(cfg, "skills", "max_tokens_per_request"))


def skills_bm25_node_fallback_threshold(config: dict[str, Any] | None = None) -> int:
    cfg = _resolve_config(config)
    return int(_merged_at(cfg, "skills", "bm25_node_fallback_threshold"))


def skills_hook_request_budget_fraction(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "hook", "request_budget_fraction"))


def skills_hook_inject_cap_multiplier(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "hook", "inject_cap_multiplier_of_request_tokens"))


def skills_hook_cursor_rule_file_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "skills", "hook", "cursor_rule_file", "enabled"))


def skills_hook_agent_interceptor_enabled(config: dict[str, Any] | None = None) -> bool:
    cfg = _resolve_config(config)
    return bool(_merged_at(cfg, "skills", "hook", "agent_interceptor", "enabled"))


def skills_hook_agent_interceptor_min_items(config: dict[str, Any] | None = None) -> int:
    cfg = _resolve_config(config)
    return int(_merged_at(cfg, "skills", "hook", "agent_interceptor", "min_items"))


def skills_proxy_request_budget_fraction(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "proxy", "request_budget_fraction"))


def skills_proxy_inject_cap_fraction(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "proxy", "inject_cap_fraction_of_savings"))


def skills_proxy_savings_budget_fraction(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "proxy", "savings_budget_fraction"))


def skills_proxy_savings_rate_threshold(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "proxy", "savings_rate_threshold"))


def skills_frontmatter_upper_limit(config: dict[str, Any] | None = None) -> float:
    cfg = _resolve_config(config)
    return float(_merged_at(cfg, "skills", "frontmatter_upper_limit"))


def skills_directories(config: dict[str, Any] | None = None) -> list[str]:
    cfg = _resolve_config(config)
    dirs = _merged_at(cfg, "skills", "directories")
    if not isinstance(dirs, list):
        raise ValueError("skills.directories must be a list")
    return [str(Path(str(d)).expanduser()) for d in dirs if d]


def skills_pageindex_config(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from cyt.indexer.pageindex import page_index_config_from_app

    cfg = _resolve_config(config)
    return page_index_config_from_app(_skills_settings(_merged_config(cfg)))


def skills_index_params_fingerprint(config: dict[str, Any] | None = None) -> str:
    import hashlib
    import json

    pageindex = skills_pageindex_config(config)
    if pageindex is None:
        return hashlib.sha256(b"{}").hexdigest()
    canonical = json.dumps(pageindex, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _user_pruning_pipeline(user_config: dict[str, Any]) -> list[str]:
    pruning = user_config.get("pruning")
    if isinstance(pruning, dict):
        tools = pruning.get("tools")
        if isinstance(tools, dict):
            sequence = tools.get("sequence")
            if isinstance(sequence, list):
                return sequence
    return []


def _model_nick_from_stage_cfg(stage_cfg: dict[str, Any]) -> str | None:
    nick = stage_cfg.get("model_nick")
    if nick:
        return str(nick)
    model = stage_cfg.get("model")
    if isinstance(model, str) and model:
        return model
    return None


def _user_stage_model_nick(user_config: dict[str, Any], stage: str) -> str | None:
    pruning = user_config.get("pruning")
    if isinstance(pruning, dict):
        tools = pruning.get("tools")
        if isinstance(tools, dict):
            pipelines = tools.get("pipelines")
            if isinstance(pipelines, dict):
                stage_cfg = pipelines.get(stage, {})
                if isinstance(stage_cfg, dict):
                    if nick := _model_nick_from_stage_cfg(stage_cfg):
                        return nick
    return None


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


def _remote_model_entries(config: dict[str, Any], model_kind: str) -> list[dict[str, Any]]:
    entries = _merged_config(config).get("models", {}).get(model_kind, {}).get("remote", [])
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def remote_model_entry(config: dict[str, Any], model_kind: str, model_nick: str) -> dict[str, Any]:
    """Return the merged ``models.<kind>.remote`` entry for *model_nick*."""
    for entry in _remote_model_entries(config, model_kind):
        if entry.get("nick") == model_nick:
            return merge_model_entry(config, entry)
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


def _append_pruning_stage_env_var(
    config: dict[str, Any],
    stage: Literal["rerank", "llm"],
    add: Callable[[str], None],
    *,
    user_config: dict[str, Any] | None = None,
) -> None:
    merged = _config_with_bundled_defaults(config)
    model_kind, _ = _PIPELINE_STAGE_MODEL_KEYS[stage]
    user = _user_overlay_for_config(config, user_config=user_config)
    model_nick = pruning_stage_model_nick(merged, stage, user_config=user)
    if not model_nick:
        raise ValueError(
            f"pruning.tools.pipelines.{stage}.model_nick is required when {stage!r} is configured",
        )
    add(key_var_name_for_model_nick(merged, model_kind, model_nick))


def _append_pipeline_stage_env_vars(
    config: dict[str, Any],
    add: Callable[[str], None],
) -> None:
    if not tools_enabled(config):
        return
    for stage in pruning_pipeline_from_config(config):
        if stage == "rerank":
            _append_pruning_stage_env_var(config, "rerank", add)
        elif stage == "llm":
            _append_pruning_stage_env_var(config, "llm", add)


def _append_skills_pipeline_env_vars(
    config: dict[str, Any],
    add: Callable[[str], None],
) -> None:
    if not skills_enabled(config):
        return
    pipeline = skills_pipeline(config).strip().lower()
    if pipeline == "rerank":
        _append_pruning_stage_env_var(config, "rerank", add)
    elif pipeline == "llm":
        _append_pruning_stage_env_var(config, "llm", add)


def required_skills_env_var_names(config: dict[str, Any]) -> list[str]:
    """Environment variable names required by the configured skills pruner pipeline."""
    merged = _config_with_bundled_defaults(config)
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    _append_skills_pipeline_env_vars(merged, add)
    return required


def required_executor_skill_env_var_names(config: dict[str, Any]) -> list[str]:
    """Env vars for executor skill pruning when directory skills are disabled."""
    if not uses_executor_tool_catalog(config):
        return []
    merged = _config_with_bundled_defaults(config)
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    pipeline = skills_pipeline(merged).strip().lower()
    if pipeline == "rerank":
        _append_pruning_stage_env_var(merged, "rerank", add)
    elif pipeline == "llm":
        _append_pruning_stage_env_var(merged, "llm", add)
    return required


def required_pruning_env_var_names(config: dict[str, Any]) -> list[str]:
    """Environment variable names required by the configured tool pruning pipeline."""
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    _append_pipeline_stage_env_vars(config, add)
    return required


def required_proxy_env_var_names(config: dict[str, Any]) -> list[str]:
    """Environment variable names required by configured tool and skills pruners."""
    required: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            required.append(name)

    for name in required_pruning_env_var_names(config):
        add(name)
    for name in required_skills_env_var_names(config):
        add(name)

    return required


def missing_proxy_env_var_names(config: dict[str, Any]) -> list[str]:
    """Return unset env var names required by the configured pruning pipeline."""
    load_proxy_env()
    return [name for name in required_proxy_env_var_names(config) if not os.environ.get(name)]


def format_proxy_env_help(missing: list[str]) -> str:
    """Human-readable guidance when pruning pipeline API keys are unset."""
    vars_block = "\n".join(f"\t{name}" for name in missing)
    env_locations = "\n".join(f"\t{p}" for p in (cwd_env_path(), USER_ENV_PATH))
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
    """Resolve stage threshold from ``pruning.tools.policy.minimum_tools``."""
    cfg = _resolve_config(config)
    merged = _merged_config(cfg)
    user = _user_overlay_for_config(cfg, user_config=user_config)

    shared = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "policy", "minimum_tools"),
    )
    if shared is not None:
        return int(cast(int | str, shared))

    stage_specific = _resolve_user_then_merged(
        merged,
        user,
        keys=("pruning", "tools", "pipelines", stage, "minimum_tools"),
    )
    if stage_specific is not None:
        return int(cast(int | str, stage_specific))

    return int(_require_nested(merged, "pruning", "tools", "policy", "minimum_tools"))


def tools_selector_soft_budget(config: dict[str, Any] | None = None) -> int:
    """Resolve LLM tools selector soft budget from ``pruning.tools.selector_soft_budget``."""
    cfg = _resolve_config(config)
    return int(_merged_at(cfg, "pruning", "tools", "selector_soft_budget"))


def skills_selector_soft_budget(config: dict[str, Any] | None = None) -> int:
    """Resolve LLM skills selector soft budget from ``skills.selector_soft_budget``."""
    cfg = _resolve_config(config)
    return int(_merged_at(cfg, "skills", "selector_soft_budget"))


def max_prune_batch_workers(config: dict[str, Any] | None = None) -> int:
    """Resolve parallel prune worker cap from config or ``CYT_MAX_PRUNE_BATCH_WORKERS``."""
    import os

    env_raw = os.environ.get("CYT_MAX_PRUNE_BATCH_WORKERS", "").strip()
    if env_raw:
        try:
            return max(1, int(env_raw))
        except ValueError:
            pass
    cfg = _resolve_config(config)
    raw = _merged_at(cfg, "pruning", "max_batch_workers")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return int(_default_at("pruning", "max_batch_workers"))


def selector_bulk_max_tokens(
    config: dict[str, Any] | None = None,
    *,
    selector_kind: str = "tools",
) -> int:
    """Resolve LLM selector bulk token limit from config."""
    cfg = _resolve_config(config)
    if selector_kind == "skills":
        value = _merged_at(cfg, "skills", "selector_bulk_max_tokens")
    else:
        value = _merged_at(cfg, "pruning", "tools", "selector_bulk_max_tokens")
    return int(value)


def llm_minimum_tools(
    config: dict[str, Any] | None = None,
    *,
    user_config: dict[str, Any] | None = None,
) -> int:
    """Resolve LLM stage threshold from ``pruning.tools.policy.minimum_tools``."""
    return _stage_minimum_tools(config, "llm", user_config=user_config)


def reranker_minimum_tools(
    config: dict[str, Any] | None = None,
    *,
    user_config: dict[str, Any] | None = None,
) -> int:
    """Resolve rerank threshold from ``pruning.tools.policy.minimum_tools``."""
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


def _normalize_provider_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    if "provider_nick" in item:
        nick = str(item["provider_nick"])
        provider = item.get("provider") or item.get("name") or nick
        return {**item, "provider": provider}
    if len(item) == 1:
        key, nested = next(iter(item.items()))
        if isinstance(nested, dict):
            nick = str(nested.get("provider_nick", key))
            provider = nested.get("provider") or nested.get("name") or key
            return {**nested, "provider_nick": nick, "provider": provider}
    return None


def provider_dns_matches_domain(provider_dns_name: str, domain: str) -> bool:
    """True when *provider_dns_name* equals *domain* or is a subdomain of it."""
    dns = provider_dns_name.strip().lower()
    dom = domain.strip().lower()
    if not dns or not dom:
        return False
    if dns == dom:
        return True
    return dns.endswith("." + dom) or dom.endswith("." + dns)


def provider_dns_matches_any(
    provider_dns_name: str,
    domains: list[Any],
) -> bool:
    """True when *provider_dns_name* matches any hostname in *domains*."""
    return any(provider_dns_matches_domain(provider_dns_name, str(domain)) for domain in domains)


def provider_nick_for_dns(config: dict[str, Any], provider_dns_name: str | None) -> str | None:
    """Return a ``provider_nick`` whose ``domain_match`` covers *provider_dns_name*."""
    if not provider_dns_name:
        return None
    for nick, entry in provider_registry(config).items():
        domain_match = entry.get("domain_match")
        if isinstance(domain_match, list) and provider_dns_matches_any(
            provider_dns_name,
            domain_match,
        ):
            return nick
    return None


def _iter_normalized_providers(
    providers: object,
) -> Iterator[tuple[str, dict[str, Any]]]:
    if not isinstance(providers, list):
        return
    for item in providers:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_provider_entry(cast(dict[str, Any], item))
        if normalized is None:
            continue
        nick = normalized.get("provider_nick")
        if nick:
            yield str(nick), normalized


def provider_registry(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize ``models.providers`` list entries to a ``provider_nick`` → fields map."""
    registry: dict[str, dict[str, Any]] = {}
    bundled_providers = _load_bundled_defaults_yaml().get("models", {}).get("providers", [])
    for nick, normalized in _iter_normalized_providers(bundled_providers):
        registry[nick] = normalized

    user_providers = config.get("models", {}).get("providers", [])
    for nick, normalized in _iter_normalized_providers(user_providers):
        registry[nick] = deep_merge(registry.get(nick, {}), normalized)
    return registry


def bundled_provider_registry() -> dict[str, dict[str, Any]]:
    """Bundled ``models.providers`` only (no user overlay)."""
    registry: dict[str, dict[str, Any]] = {}
    bundled_providers = _load_bundled_defaults_yaml().get("models", {}).get("providers", [])
    for nick, normalized in _iter_normalized_providers(bundled_providers):
        registry[nick] = normalized
    return registry


def _litellm_provider_for_entry(
    *,
    provider_nick: str | None,
    entry_provider: object | None,
    registry_provider: object | None,
) -> str | None:
    """Resolve a LiteLLM provider slug; never treat ``provider_nick`` as the slug."""
    nick = str(provider_nick).strip() if provider_nick else ""
    for candidate in (entry_provider, registry_provider):
        if isinstance(candidate, str):
            value = candidate.strip()
            if value and value != nick:
                return value
    if nick:
        bundled = bundled_provider_registry().get(nick, {})
        bundled_provider = bundled.get("provider")
        if isinstance(bundled_provider, str):
            value = bundled_provider.strip()
            if value and value != nick:
                return value
    return None


def merge_model_entry(config: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Merge provider registry fields into a model catalog row (inline fields win)."""
    nick = entry.get("provider_nick") or entry.get("provider")
    base: dict[str, Any] = {}
    if nick:
        base = dict(provider_registry(config).get(str(nick), {}))
    merged = {**base, **entry}
    provider = _litellm_provider_for_entry(
        provider_nick=str(nick) if nick else None,
        entry_provider=entry.get("provider"),
        registry_provider=base.get("provider"),
    )
    if provider:
        merged["provider"] = provider
    return merged


def provider_name_from_nick(config: dict[str, Any], provider_nick: str | None) -> str | None:
    """Return the LiteLLM ``provider`` for *provider_nick* from the provider registry."""
    if not provider_nick:
        return None
    nick = str(provider_nick).strip()
    if not nick:
        return None
    registry_entry = provider_registry(config).get(nick)
    if not registry_entry:
        return None
    provider = registry_entry.get("provider")
    return str(provider) if provider else None


def stats_provider_for_entry(config: dict[str, Any], entry: dict[str, Any]) -> str | None:
    """Resolve the provider name persisted in stats via registry ``provider_nick``."""
    provider_nick = entry.get("provider_nick")
    if provider_nick:
        return provider_name_from_nick(config, str(provider_nick))
    legacy = entry.get("provider")
    if legacy:
        return provider_name_from_nick(config, str(legacy))
    return None


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
    merged = _merged_config(_resolve_config(config))
    for entry in merged.get("models", {}).get(model_kind, {}).get(model_type, []):
        if entry.get("nick") == model_nick:
            if model_type == "remote":
                enriched = merge_model_entry(merged, entry)
                load_proxy_env()
                key_var_name = enriched.get("key_var_name")
                api_key_value = os.environ.get(key_var_name) if key_var_name else None
                base_url = enriched.get("base_url")
                if enriched.get("provider"):
                    return litellm_model_name(enriched), api_key_value, base_url
                raise ValueError(
                    f"Unknown remote provider for nick: {model_nick}, kind: {model_kind}, type: {model_type} in LiteLLM format",
                )
            full_model_name = entry.get("name")
            return f"{full_model_name}", None, None
    raise ValueError(f"Unknown model nick: {model_nick}, kind: {model_kind}, type: {model_type}")
