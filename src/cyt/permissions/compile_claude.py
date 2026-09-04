"""Compile cyt-native permissions to Claude Code settings.json format."""

from __future__ import annotations

from cyt.permissions.match import parse_mcp_deny_entry, parse_skill_permission_entry
from cyt.permissions.schema import EffectivePermissions


def compile_mcp_deny_entry(entry: str) -> str:
    rule = parse_mcp_deny_entry(entry)
    if rule is None:
        return f"mcp__{entry}"
    if rule.kind == "tool" and rule.tool is not None:
        return f"mcp__{rule.server}__{rule.tool}"
    if rule.kind == "server_wildcard":
        return f"mcp__{rule.server}__*"
    return f"mcp__{rule.server}"


def compile_mcp_allow_entry(entry: str) -> str:
    return compile_mcp_deny_entry(entry)


def compile_skill_deny_entry(entry: str) -> str | None:
    rule = parse_skill_permission_entry(entry)
    if rule is None or rule.kind == "path":
        return None
    text = rule.value
    if text.startswith("Skill(") and text.endswith(")"):
        return text
    return f"Skill({text})"


def compile_skill_allow_entry(entry: str) -> str | None:
    return compile_skill_deny_entry(entry)


def compile_claude_permissions(effective: EffectivePermissions) -> dict[str, list[str]]:
    deny: list[str] = []
    allow: list[str] = []
    for entry in effective.mcp.deny:
        deny.append(compile_mcp_deny_entry(entry))
    for entry in effective.mcp.allow:
        allow.append(compile_mcp_allow_entry(entry))
    for entry in effective.skills.deny:
        compiled = compile_skill_deny_entry(entry)
        if compiled is not None:
            deny.append(compiled)
    for entry in effective.skills.allow:
        compiled = compile_skill_allow_entry(entry)
        if compiled is not None:
            allow.append(compiled)
    return {"deny": deny, "allow": allow}
