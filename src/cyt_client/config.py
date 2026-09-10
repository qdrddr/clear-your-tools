"""Read CYT user config values needed by cyt-client (stdlib only)."""

from __future__ import annotations

from pathlib import Path

USER_CONFIG_PATH = Path("~/.config/cyt/config.yaml")
CWD_CONFIG_NAME = "config.yaml"
_DEFAULT_CURSOR_RULE_FILE_ENABLED = True


def _parse_bool(raw: str) -> bool | None:
    value = raw.strip().strip('"').strip("'").casefold()
    if value in {"true", "yes", "on", "1"}:
        return True
    if value in {"false", "no", "off", "0"}:
        return False
    return None


def _nested_bool_from_yaml(text: str, path: tuple[str, ...]) -> bool | None:
    """Best-effort read of a nested bool from simple YAML mappings."""
    if not path:
        return None

    stack: list[tuple[int, str]] = []

    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()

        if len(stack) >= len(path) or key != path[len(stack)]:
            continue

        if len(stack) + 1 == len(path):
            return _parse_bool(value) if value else None

        if value:
            continue
        stack.append((indent, key))

    return None


def _nested_bool_from_yaml_paths(text: str, paths: tuple[tuple[str, ...], ...]) -> bool | None:
    for path in paths:
        value = _nested_bool_from_yaml(text, path)
        if value is not None:
            return value
    return None


def resolve_config_path() -> Path:
    """Match ``cyt.config.resolve_config_path`` file selection (no explicit path)."""
    cwd_config = Path.cwd() / CWD_CONFIG_NAME
    if cwd_config.exists():
        return cwd_config
    return USER_CONFIG_PATH.expanduser()


def _nested_scalar_from_yaml(text: str, path: tuple[str, ...]) -> str | None:
    if not path:
        return None
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if len(stack) >= len(path) or key != path[len(stack)]:
            continue
        if len(stack) + 1 == len(path):
            return value or None
        if value:
            continue
        stack.append((indent, key))
    return None


def _nested_scalar_from_yaml_paths(text: str, paths: tuple[tuple[str, ...], ...]) -> str | None:
    for path in paths:
        value = _nested_scalar_from_yaml(text, path)
        if value:
            return value
    return None


def tools_from_includes_cyt_mcp() -> bool:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    raw = _nested_scalar_from_yaml_paths(
        text,
        (
            ("tools", "hook", "tools_from"),
            ("pruning", "tools", "hook", "tools_from"),
        ),
    )
    if raw:
        normalized = raw.replace("-", "_").casefold()
        return normalized in {"cyt_mcp", "cytmcp"}
    if "cyt_mcp" in text or "cyt-mcp" in text:
        return "tools_from" in text
    return False


def skills_hook_agent_interceptor_enabled() -> bool:
    """Return ``skills.hook.agent_interceptor.enabled`` (default: false)."""
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    value = _nested_bool_from_yaml(
        text,
        ("skills", "hook", "agent_interceptor", "enabled"),
    )
    return bool(value)


def skills_hook_cursor_rule_file_enabled() -> bool:
    """Return cursor rule-file hook flag (canonical or legacy path; default: true)."""
    config_path = resolve_config_path()
    if not config_path.is_file():
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED

    value = _nested_bool_from_yaml_paths(
        text,
        (
            ("agents", "cursor", "hook", "cursor_rule_file", "enabled"),
            ("skills", "hook", "cursor_rule_file", "enabled"),
        ),
    )
    if value is None:
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED
    return value


def _inject_via_for_agent_from_yaml(text: str, agent: str) -> str | None:
    """Read per-agent inject_via from canonical or legacy YAML paths."""
    canonical = _nested_scalar_from_yaml(text, ("agents", agent, "tools", "inject_via"))
    if canonical:
        return canonical.casefold()
    legacy = _nested_scalar_from_yaml(text, ("pruning", "inject_via", agent))
    if legacy:
        return legacy.casefold()
    return None


def inject_via_for_agent(agent: str) -> str:
    """Return hook or proxy for *agent* (cursor always defaults hook)."""
    config_path = resolve_config_path()
    if not config_path.is_file():
        return "hook" if agent == "cursor" else "proxy"
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return "hook" if agent == "cursor" else "proxy"
    mode = _inject_via_for_agent_from_yaml(text, agent)
    if mode in {"hook", "proxy"}:
        return mode
    return "hook" if agent == "cursor" else "proxy"


def hallucination_gate_enabled() -> bool:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    value = _nested_bool_from_yaml_paths(
        text,
        (
            ("defaults", "hallucination_gate", "enabled"),
            ("hallucination_gate", "enabled"),
        ),
    )
    return bool(value)


def skills_enabled() -> bool:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    value = _nested_bool_from_yaml(text, ("skills", "enabled"))
    if value is None:
        return False
    return value


def tools_enabled() -> bool:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    value = _nested_bool_from_yaml_paths(
        text,
        (
            ("tools", "enabled"),
            ("pruning", "tools", "enabled"),
        ),
    )
    if value is None:
        return False
    return value


def verify_only_mode() -> bool:
    return hallucination_gate_enabled() and not skills_enabled() and not tools_enabled()


def tool_examples_post_tool_capture_enabled() -> bool:
    """Return whether postToolUse example capture is enabled (defaults: true)."""
    config_path = resolve_config_path()
    if not config_path.is_file():
        return True
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return True
    enabled = _nested_bool_from_yaml(text, ("tools", "examples", "enabled"))
    if enabled is False:
        return False
    post_tool_use = _nested_bool_from_yaml(text, ("tools", "examples", "capture", "post_tool_use"))
    if post_tool_use is False:
        return False
    return True
