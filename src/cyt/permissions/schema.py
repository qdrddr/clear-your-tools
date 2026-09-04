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


def _string_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    items: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return tuple(items)
