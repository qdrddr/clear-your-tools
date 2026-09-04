"""Datatypes for cyt-native MCP and skills permissions."""

from __future__ import annotations

from dataclasses import dataclass, field

AgentName = str  # cursor | claude | codex
ScopeName = str  # global | workspace


@dataclass(frozen=True)
class PermissionLists:
    deny: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, raw: object) -> PermissionLists:
        if not isinstance(raw, dict):
            return cls()
        deny = _string_list(raw.get("deny"))
        allow = _string_list(raw.get("allow"))
        return cls(deny=deny, allow=allow)


@dataclass(frozen=True)
class McpPermissions:
    deny: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillsPermissions:
    deny: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectivePermissions:
    mcp: McpPermissions = field(default_factory=McpPermissions)
    skills: SkillsPermissions = field(default_factory=SkillsPermissions)


def _normalize_permission_entry(item: object) -> str | None:
    """Normalize a YAML deny/allow list item to a cyt permission string."""
    if isinstance(item, dict):
        if len(item) != 1:
            return None
        key, value = next(iter(item.items()))
        key_text = str(key).strip().lower()
        value_text = str(value or "").strip()
        if not value_text:
            return None
        if key_text == "path":
            return f"path:{value_text}"
        if key_text == "name":
            return value_text
        return None
    text = str(item or "").strip()
    return text or None


def _string_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[str] = []
    for item in raw:
        text = _normalize_permission_entry(item)
        if text:
            items.append(text)
    return tuple(items)
