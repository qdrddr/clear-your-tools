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


def tools_from_includes_cyt_mcp() -> bool:
    config_path = resolve_config_path()
    if not config_path.is_file():
        return False
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return False
    raw = _nested_scalar_from_yaml(text, ("pruning", "tools", "hook", "tools_from"))
    if raw:
        normalized = raw.replace("-", "_").casefold()
        return normalized in {"cyt_mcp", "cytmcp"}
    if "cyt_mcp" in text or "cyt-mcp" in text:
        return "tools_from" in text
    return False


def skills_hook_cursor_rule_file_enabled() -> bool:
    """Return ``skills.hook.cursor_rule_file.enabled`` (default: true)."""
    config_path = resolve_config_path()
    if not config_path.is_file():
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED

    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED

    value = _nested_bool_from_yaml(
        text,
        ("skills", "hook", "cursor_rule_file", "enabled"),
    )
    if value is None:
        return _DEFAULT_CURSOR_RULE_FILE_ENABLED
    return value
