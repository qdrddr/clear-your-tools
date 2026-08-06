"""Proxy-path session JSONL writer for Type-1/Type-2 injection entries."""

from __future__ import annotations

from typing import Any

from cyt.config import inject_via_for_agent
from cyt.injection.verify_session_log import filter_new_tool_entries
from cyt.proxy.verify_session_log import session_log_path_for_agent


def persist_proxy_session_log_entries(
    *,
    agent: str,
    session_id: str | None,
    entries: list[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """Append hook-style session log entries when proxy owns injection."""
    if inject_via_for_agent(config, agent) != "proxy":
        return
    if not session_id or not session_id.strip() or not entries:
        return
    path = session_log_path_for_agent(agent, session_id.strip())
    if path is None:
        return
    from cyt_client.sessions import append_session_log, append_tool_catalog_entries

    tool_entries = [entry for entry in entries if entry.get("kind") != "tool_catalog"]
    catalog_entries = [entry for entry in entries if entry.get("kind") == "tool_catalog"]
    deduped_tools = filter_new_tool_entries(path, tool_entries)
    if deduped_tools:
        append_session_log(path, deduped_tools, agent=agent)
    if catalog_entries:
        append_tool_catalog_entries(path, catalog_entries, agent=agent)
