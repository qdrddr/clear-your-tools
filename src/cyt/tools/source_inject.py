"""Per-source hook tool injection sections inside one ``<agent-tools>`` block."""

from __future__ import annotations

from typing import Any

from cyt.injection.header_pre_exposed import (
    agent_tools_intro_pre_exposed,
    cyt_mcp_note_pre_exposed,
)
from cyt.tools.inject import (
    _AGENT_TOOLS_DESCRIPTION_BASE,
    _EXECUTOR_WORKSPACE_NOTE,
    _agent_tools_open_tag,
    _format_workspace_roots_block,
    ensure_agent_tools_starts_on_new_line,
    format_tool_item,
)
from cyt.tools.mcpc_inject import (
    _MCPC_WORKSPACE_NOTE,
    _format_mcpc_section,
    _format_server_block,
    _group_tools_by_session,
    compute_mcpc_pre_exposure_flags,
)

_CLOUDFLARE_WORKSPACE_NOTE = (
    "Listed below are pre-filtered upstream MCP tools and their relevant definitions, previously retrieved via `portal_list_servers`. "
    "Use this information to skip any additional `portal_list_servers` call and jump to using the listed tools directly or with code_mode. "
    "Do not use `portal_list_servers` unless one of the following is true:\n"
    "1. The task explicitly requires the full list of **all available** MCP servers.\n"
    "2. The task requires tool definitions not included in the pre-filtered tool definitions listed below.\n"
)

_CYT_MCP_WORKSPACE_NOTE = (
    "Listed below are the pre-filtered tool definitions and relevant optional properties for this request. "
    "Use the listed definitions directly without retrieving the full tool definitions. "
    "Do not use `get-tool-definitions` unless one of the following is true:\n"
    "1. The task requires a tool that is not included in the pre-filtered definitions below.\n"
    "2. The task requires optional properties or complete tool definitions that were omitted by the pruning pipeline.\n"
)

_CYT_MCP_EMPTY_NOTE = (
    "No relevant cyt-mcp tools matched this prompt. Do not use `get-tool-definitions` — "
    "there is nothing to look up. This block is kept stable for prompt-prefix cache."
)


def _normalize_tool_scope(tool: dict[str, Any]) -> str:
    raw = tool.get("cyt_catalog_scope")
    if isinstance(raw, str) and raw.strip().lower() == "workspace":
        return "workspace"
    return "user"


def _partition_cyt_mcp_tools_by_scope(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    workspace_tools = [tool for tool in tools if _normalize_tool_scope(tool) == "workspace"]
    ws_names = {
        str(tool.get("name") or "") for tool in workspace_tools if str(tool.get("name") or "")
    }
    user_tools = [
        tool
        for tool in tools
        if _normalize_tool_scope(tool) != "workspace"
        and str(tool.get("name") or "") not in ws_names
    ]
    return workspace_tools, user_tools


def _format_scope_tool_block(
    tag: str,
    tools: list[dict[str, Any]],
    *,
    include_tool_description: bool = True,
) -> str:
    if not tools:
        return ""
    item_lines = [
        line
        for tool in tools
        if (line := format_tool_item(tool, include_tool_description=include_tool_description))
    ]
    if not item_lines:
        return ""
    body = "\n".join(item_lines)
    return f"<{tag}>\n{body}\n</{tag}>"


def format_cloudflare_source_section(
    tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
    include_tool_description: bool = True,
) -> str:
    if not tools:
        return ""
    item_lines = [
        line
        for tool in tools
        if (line := format_tool_item(tool, include_tool_description=include_tool_description))
    ]
    if not item_lines:
        return ""
    body = "\n".join(item_lines)
    prompt = _CLOUDFLARE_WORKSPACE_NOTE
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) > 1:
        roots = _format_workspace_roots_block(paths)
        if roots:
            prompt = f"{prompt}\n{roots}"
    return _join_section(prompt, "cloudflare", body)


_DEFINITIONS_WORKSPACE_NOTE = (
    "These tools come from the static definitions file configured for hook injection. "
    "Use the current project's workspace_roots as the working directory when relevant."
)


def _join_section(prompt: str, inner_tag: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    prompt = prompt.strip()
    inner = f"{prompt}\n{body}" if prompt else body
    return f"<{inner_tag}>\n{inner}\n</{inner_tag}>"


def format_mcp_source_section(
    tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
    session_text: str = "",
    surviving_instruction_sessions: set[str] | None = None,
) -> str:
    """Return ``<mcpc>…</mcpc>`` body with MCPC CLI note, or empty string."""
    if not tools:
        return ""
    pre_exposure = compute_mcpc_pre_exposure_flags(tools, session_text)
    grouped = _group_tools_by_session(tools)
    server_blocks = [
        _format_server_block(
            session,
            session_tools,
            surviving_instruction_sessions=surviving_instruction_sessions,
            pre_exposure=pre_exposure,
        )
        for session, session_tools in grouped.items()
    ]
    body = "\n".join(block for block in server_blocks if block)
    if not body.strip():
        return ""
    prompt = "" if _MCPC_WORKSPACE_NOTE.strip() in session_text else _MCPC_WORKSPACE_NOTE
    if prompt and workspace_paths:
        paths = [path.strip() for path in workspace_paths if path.strip()]
        if len(paths) > 1:
            roots = _format_workspace_roots_block(paths)
            if roots:
                prompt = f"{prompt}\n{roots}"
    return _format_mcpc_section(prompt=prompt, body=body)


def format_executor_source_section(
    tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
    include_tool_description: bool = True,
) -> str:
    """Return executor prompt + ``<executor>…</executor>`` body."""
    if not tools:
        return ""
    item_lines = [
        line
        for tool in tools
        if (line := format_tool_item(tool, include_tool_description=include_tool_description))
    ]
    if not item_lines:
        return ""
    body = "\n".join(item_lines)
    prompt = _EXECUTOR_WORKSPACE_NOTE
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) > 1:
        roots = _format_workspace_roots_block(paths)
        if roots:
            prompt = f"{prompt}\n{roots}"
    return _join_section(prompt, "executor", body)


def format_definitions_source_section(
    tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
    include_tool_description: bool = True,
) -> str:
    """Return definitions prompt + ``<definitions>…</definitions>`` body."""
    if not tools:
        return ""
    item_lines = [
        line
        for tool in tools
        if (line := format_tool_item(tool, include_tool_description=include_tool_description))
    ]
    if not item_lines:
        return ""
    body = "\n".join(item_lines)
    prompt = _DEFINITIONS_WORKSPACE_NOTE
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    if len(paths) > 1:
        roots = _format_workspace_roots_block(paths)
        if roots:
            prompt = f"{prompt}\n{roots}"
    return _join_section(prompt, "definitions", body)


def format_cyt_mcp_source_section(
    tools: list[dict[str, Any]],
    *,
    workspace_paths: list[str] | None = None,
    include_tool_description: bool = True,
    session_text: str = "",
) -> str:
    if not tools:
        return f"<cyt-mcp>\n{_CYT_MCP_EMPTY_NOTE}\n</cyt-mcp>"

    workspace_tools, user_tools = _partition_cyt_mcp_tools_by_scope(tools)
    ws_block = _format_scope_tool_block(
        "cyt-mcp-ws",
        workspace_tools,
        include_tool_description=include_tool_description,
    )
    usr_block = _format_scope_tool_block(
        "cyt-mcp-usr",
        user_tools,
        include_tool_description=include_tool_description,
    )
    if not ws_block and not usr_block:
        return ""

    prompt = ""
    if not cyt_mcp_note_pre_exposed(session_text, _CYT_MCP_WORKSPACE_NOTE):
        prompt = _CYT_MCP_WORKSPACE_NOTE
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    roots = ""
    if len(paths) > 1:
        roots = _format_workspace_roots_block(paths)

    inner_parts = [part for part in (prompt.strip(), roots, ws_block, usr_block) if part]
    inner = "\n".join(inner_parts)
    return f"<cyt-mcp>\n{inner}\n</cyt-mcp>"


def _multi_source_agent_tools_description() -> str:
    return (
        f"{_AGENT_TOOLS_DESCRIPTION_BASE} Multiple tool sources are grouped in "
        "<mcpc>, <cyt-mcp>, <cloudflare>, <executor>, and <definitions> sections."
    )


def format_multi_source_agent_tools(
    sections: dict[str, str],
    *,
    workspace_paths: list[str] | None = None,
    session_text: str = "",
) -> str:
    """Join non-empty source sections inside one ``<agent-tools>`` wrapper."""
    ordered = [
        sections.get("cyt_mcp", "").strip(),
        sections.get("mcpc", "").strip(),
        sections.get("cloudflare", "").strip(),
        sections.get("executor", "").strip(),
        sections.get("definitions", "").strip(),
    ]
    inner = "\n".join(part for part in ordered if part)
    if not inner.strip():
        return ""
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    lines = [_agent_tools_open_tag(workspace_paths=paths)]
    description = _multi_source_agent_tools_description()
    if not agent_tools_intro_pre_exposed(session_text, description):
        lines.append(description)
    lines.append(inner)
    lines.append("</agent-tools>")
    wrapped = "\n".join(lines)
    return ensure_agent_tools_starts_on_new_line(wrapped)
