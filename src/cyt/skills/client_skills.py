"""Hook payload skills supplied by cyt-client.

The hook daemon treats ``cyt_skills`` on the hook payload as authoritative.
Discovery of workspace, user/global, and platform skill directories happens in
``cyt_client`` only; the daemon must not scan config directories on the hook path.
"""

from __future__ import annotations

from typing import Any

from cyt.launch.upstream import AgentName
from cyt.skills.catalog import SkillEntryRef, build_registry


def client_skills_from_payload(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    """Return parsed client skills when ``cyt_skills`` is present on the hook payload."""
    if "cyt_skills" not in payload:
        return None
    raw = payload.get("cyt_skills")
    if not isinstance(raw, list):
        return []

    skills: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        content = item.get("content")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(content, str):
            continue
        skills.append({"path": path.strip(), "content": content})
    return skills


def build_registry_for_hook_payload(
    config: dict[str, Any],
    payload: dict[str, Any] | None,
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
) -> list[SkillEntryRef]:
    """Build the hook skills registry from cyt-client payload content only."""
    if payload is None or "cyt_skills" not in payload:
        client_skills: list[dict[str, str]] = []
    else:
        parsed = client_skills_from_payload(payload)
        client_skills = parsed if parsed is not None else []
    return build_registry(
        config,
        agent=agent,
        upstream_kind=upstream_kind,
        client_skills=client_skills,
    )
