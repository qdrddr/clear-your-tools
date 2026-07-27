"""Per-source hook tool injection sections inside one ``<agent-tools>`` block."""

from __future__ import annotations

from typing import Any

from cyt.tools.inject import (
    _AGENT_TOOLS_DESCRIPTION_BASE,
    _EXECUTOR_WORKSPACE_NOTE,
    _format_workspace_roots_block,
    _xml_single_quoted_attr,
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

_DEFINITIONS_WORKSPACE_NOTE = (
    "These tools come from the static definitions file configured for hook injection. "
    "Use the current project's workspace_roots as the working directory when relevant."
)


def _join_section(prompt: str, inner_tag: str, body: str) -> str:
    body = body.strip()
    if not body:
        return ""
    prompt = prompt.strip()
    if prompt:
        return f"{prompt}\n<{inner_tag}>\n{body}\n</{inner_tag}>"
    return f"<{inner_tag}>\n{body}\n</{inner_tag}>"


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


def format_multi_source_agent_tools(
    sections: dict[str, str],
    *,
    workspace_paths: list[str] | None = None,
) -> str:
    """Join non-empty source sections inside one ``<agent-tools>`` wrapper."""
    ordered = [
        sections.get("mcpc", "").strip(),
        sections.get("executor", "").strip(),
        sections.get("definitions", "").strip(),
    ]
    inner = "\n".join(part for part in ordered if part)
    if not inner.strip():
        return ""
    description = (
        f"{_AGENT_TOOLS_DESCRIPTION_BASE} Multiple tool sources are grouped in "
        "<mcpc>, <executor>, and <definitions> sections."
    )
    paths = [path.strip() for path in (workspace_paths or []) if path.strip()]
    attrs = [f"description='{_xml_single_quoted_attr(description)}'"]
    if len(paths) == 1:
        attrs.append(f"path='{_xml_single_quoted_attr(paths[0])}'")
    open_tag = f"<agent-tools {' '.join(attrs)}>"
    wrapped = "\n".join([open_tag, inner, "</agent-tools>"])
    return ensure_agent_tools_starts_on_new_line(wrapped)
