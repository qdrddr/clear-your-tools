"""Match cyt-native permission rules against MCP servers/tools and skills."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

McpRuleKind = Literal["server", "server_wildcard", "tool"]
SkillRuleKind = Literal["name", "path"]

SKILL_PATH_PREFIX = "path:"


@dataclass(frozen=True)
class ParsedSkillPermissionRule:
    raw: str
    kind: SkillRuleKind
    value: str


@dataclass(frozen=True)
class ParsedMcpDenyRule:
    raw: str
    kind: McpRuleKind
    server: str
    tool: str | None = None


def parse_mcp_deny_entry(entry: str) -> ParsedMcpDenyRule | None:
    text = str(entry or "").strip()
    if not text:
        return None
    if text.endswith("/*"):
        server = text[:-2].strip()
        if not server:
            return None
        return ParsedMcpDenyRule(raw=text, kind="server_wildcard", server=server)
    if "/" in text:
        server, tool = text.split("/", 1)
        server = server.strip()
        tool = tool.strip()
        if not server or not tool:
            return None
        return ParsedMcpDenyRule(raw=text, kind="tool", server=server, tool=tool)
    return ParsedMcpDenyRule(raw=text, kind="server", server=text)


def parse_mcp_deny_rules(entries: tuple[str, ...] | list[str]) -> list[ParsedMcpDenyRule]:
    parsed: list[ParsedMcpDenyRule] = []
    for entry in entries:
        rule = parse_mcp_deny_entry(entry)
        if rule is not None:
            parsed.append(rule)
    return parsed


def is_mcp_server_denied(server: str, deny_entries: tuple[str, ...] | list[str]) -> bool:
    name = str(server or "").strip()
    if not name:
        return False
    for rule in parse_mcp_deny_rules(deny_entries):
        if rule.kind in {"server", "server_wildcard"} and rule.server == name:
            return True
    return False


def is_mcp_tool_denied(
    server: str,
    tool: str,
    deny_entries: tuple[str, ...] | list[str],
) -> bool:
    server_name = str(server or "").strip()
    tool_name = str(tool or "").strip()
    if not server_name or not tool_name:
        return False
    if is_mcp_server_denied(server_name, deny_entries):
        return True
    for rule in parse_mcp_deny_rules(deny_entries):
        if rule.kind == "tool" and rule.server == server_name and rule.tool == tool_name:
            return True
    return False


def explicit_denied_servers(deny_entries: tuple[str, ...] | list[str]) -> list[str]:
    """Return server names with explicit server-level deny rules."""
    servers: list[str] = []
    seen: set[str] = set()
    for rule in parse_mcp_deny_rules(deny_entries):
        if rule.kind not in {"server", "server_wildcard"} or not rule.server:
            continue
        if rule.server in seen:
            continue
        seen.add(rule.server)
        servers.append(rule.server)
    return servers


def explicit_denied_tools_for_server(
    server: str,
    deny_entries: tuple[str, ...] | list[str],
) -> list[str]:
    """Return tool names with explicit ``server/tool`` deny rules."""
    server_name = str(server or "").strip()
    if not server_name:
        return []
    tools: list[str] = []
    seen: set[str] = set()
    for rule in parse_mcp_deny_rules(deny_entries):
        if rule.kind != "tool" or rule.server != server_name or not rule.tool:
            continue
        if rule.tool in seen:
            continue
        seen.add(rule.tool)
        tools.append(rule.tool)
    return tools


def split_catalog_tool_name(catalog_name: str) -> tuple[str, str] | None:
    """Split cyt-mcp catalog name ``server_tool`` into backend server + tool."""
    name = str(catalog_name or "").strip()
    if not name or "_" not in name:
        return None
    server, _, tool = name.partition("_")
    if not server or not tool:
        return None
    return server, tool


def is_catalog_tool_denied(catalog_name: str, deny_entries: tuple[str, ...] | list[str]) -> bool:
    parts = split_catalog_tool_name(catalog_name)
    if parts is None:
        return False
    server, tool = parts
    return is_mcp_tool_denied(server, tool, deny_entries)


def normalize_skill_name(name: str) -> str:
    return str(name or "").strip().casefold()


def parse_skill_permission_entry(entry: str) -> ParsedSkillPermissionRule | None:
    text = str(entry or "").strip()
    if not text:
        return None
    if text.startswith(SKILL_PATH_PREFIX):
        path_value = text.removeprefix(SKILL_PATH_PREFIX).strip()
        if not path_value:
            return None
        return ParsedSkillPermissionRule(raw=text, kind="path", value=path_value)
    return ParsedSkillPermissionRule(raw=text, kind="name", value=text)


def format_skill_path_permission_entry(
    path: str | Path,
    *,
    workspace_root: Path | None = None,
) -> str:
    text = str(path or "").strip()
    if not text:
        raise ValueError("Skill path must not be empty")
    candidate = Path(text).expanduser()
    if workspace_root is not None:
        base = workspace_root.expanduser().resolve()
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        if not candidate.is_absolute():
            resolved = (base / candidate).resolve()
        try:
            relative = resolved.relative_to(base)
            text = relative.as_posix()
        except ValueError:
            text = resolved.as_posix()
    else:
        text = candidate.as_posix()
    return f"{SKILL_PATH_PREFIX}{text}"


def _resolve_permission_path(path: str | Path, *, base: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    try:
        return candidate.resolve()
    except OSError:
        return candidate


def _skill_path_rule_kind(rule_path: Path) -> Literal["file", "dir"]:
    if rule_path.suffix.lower() == ".md":
        return "file"
    if rule_path.exists() and rule_path.is_file():
        return "file"
    return "dir"


def _normalize_rule_path_parts(rule_path: str | Path) -> tuple[str, ...]:
    text = str(rule_path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return tuple(part for part in Path(text).parts if part and part != ".")


def _find_matching_rule_segment_index(
    skill_parts: tuple[str, ...],
    rule_parts: tuple[str, ...],
) -> int | None:
    if not rule_parts or len(rule_parts) > len(skill_parts):
        return None
    for start in range(len(skill_parts) - len(rule_parts) + 1):
        end = start + len(rule_parts)
        if skill_parts[start:end] == rule_parts:
            return start
    return None


def _skill_path_matches_rule_segments(skill: Path, rule_path: str | Path) -> bool:
    skill_parts = skill.parts
    rule_parts = _normalize_rule_path_parts(rule_path)
    start = _find_matching_rule_segment_index(skill_parts, rule_parts)
    if start is None:
        return False
    matched_dir = Path(*skill_parts[: start + len(rule_parts)])
    if _skill_path_rule_kind(Path(rule_path)) == "file":
        return skill == matched_dir
    if skill == matched_dir or skill.parent == matched_dir:
        return True
    try:
        skill.relative_to(matched_dir)
    except ValueError:
        return False
    return True


def skill_path_matches_rule(
    skill_path: str | Path,
    rule_path: str | Path,
    *,
    base: Path | None = None,
) -> bool:
    skill = _resolve_permission_path(skill_path, base=base)
    rule = _resolve_permission_path(rule_path, base=base)
    if _skill_path_rule_kind(rule) == "file":
        if skill == rule:
            return True
        return _skill_path_matches_rule_segments(skill, rule_path)
    if skill == rule:
        return True
    if skill.parent == rule:
        return True
    try:
        skill.relative_to(rule)
        return True
    except ValueError:
        return _skill_path_matches_rule_segments(skill, rule_path)


def _paths_equivalent_for_permission(
    left: str | Path,
    right: str | Path,
    *,
    base: Path | None = None,
) -> bool:
    return _resolve_permission_path(left, base=base) == _resolve_permission_path(right, base=base)


def split_skill_permission_entries(
    entries: tuple[str, ...] | list[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    names: list[str] = []
    paths: list[str] = []
    for entry in entries:
        rule = parse_skill_permission_entry(entry)
        if rule is None:
            continue
        if rule.kind == "path":
            paths.append(rule.value)
        else:
            names.append(rule.value)
    return tuple(names), tuple(paths)


def is_skill_name_denied(skill_name: str, deny_entries: tuple[str, ...] | list[str]) -> bool:
    target = normalize_skill_name(skill_name)
    if not target:
        return False
    name_entries, _ = split_skill_permission_entries(deny_entries)
    for entry in name_entries:
        if normalize_skill_name(entry) == target:
            return True
    return False


def is_skill_path_denied(
    skill_path: str | Path,
    deny_entries: tuple[str, ...] | list[str],
    *,
    base: Path | None = None,
) -> bool:
    path_text = str(skill_path or "").strip()
    if not path_text:
        return False
    _, path_entries = split_skill_permission_entries(deny_entries)
    for entry in path_entries:
        if skill_path_matches_rule(path_text, entry, base=base):
            return True
    return False


def is_skill_permission_denied(
    *,
    skill_name: str,
    skill_path: str | Path | None = None,
    deny_entries: tuple[str, ...] | list[str],
    base: Path | None = None,
) -> bool:
    if is_skill_name_denied(skill_name, deny_entries):
        return True
    if skill_path is not None:
        return is_skill_path_denied(skill_path, deny_entries, base=base)
    return False


def is_skill_denied(skill_name: str, deny_entries: tuple[str, ...] | list[str]) -> bool:
    """Return whether a skill policy *name* is denied (ignores path-only rules)."""
    return is_skill_name_denied(skill_name, deny_entries)
