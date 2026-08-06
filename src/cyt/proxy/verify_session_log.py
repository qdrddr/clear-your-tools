"""Proxy-path verify-only session JSONL writer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from cyt.config import inject_via_for_agent, verify_only_mode
from cyt.injection.verify_session_log import append_verify_session_log

if TYPE_CHECKING:
    from cyt.proxy.anthropic import PruneResult
    from cyt.skills.proxy_inject import SkillsProxyInjectMeta


def session_id_from_headers(headers: Mapping[str, str]) -> str | None:
    """Extract session id from Claude/Codex proxy request headers."""
    for key in ("x-claude-code-session-id", "session-id", "Session-Id"):
        raw = headers.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def session_log_path_for_agent(agent: str, session_id: str) -> Path | None:
    from cyt_client.sessions import session_log_path

    return session_log_path(
        {
            "session_id": session_id,
            "cyt_agent": agent,
        },
    )


def _query_from_anthropic_payload(payload: dict[str, object]) -> str | None:
    from cyt.proxy.anthropic import extract_user_query

    messages = payload.get("messages")
    if isinstance(messages, list):
        return extract_user_query(cast("list[dict[str, Any]]", messages))
    return None


def _query_from_openai_payload(payload: dict[str, object]) -> str | None:
    from cyt.proxy.openai_responses import extract_user_query_from_input

    input_items = payload.get("input")
    if isinstance(input_items, list):
        return extract_user_query_from_input(cast("list[dict[str, Any]]", input_items))
    return None


def resolve_proxy_user_query(
    body: bytes,
    kind: str | None,
    *,
    skills_meta: SkillsProxyInjectMeta | None = None,
    pruning: PruneResult | None = None,
) -> str | None:
    """Best-effort user turn text from proxy upstream request body."""
    if skills_meta is not None:
        query = skills_meta.query
        if isinstance(query, str) and query.strip():
            return query.strip()
    if pruning is not None:
        query = pruning.query
        if isinstance(query, str) and query.strip():
            return query.strip()
    if not body or kind not in ("anthropic", "openai"):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if kind == "anthropic":
        return _query_from_anthropic_payload(payload)
    return _query_from_openai_payload(payload)


def _resolve_verify_tools(
    config: dict[str, Any],
    input_tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    from cyt.cyt_mcp.catalog import get_cyt_mcp_catalog

    catalog = get_cyt_mcp_catalog(config, blocking=False)
    if catalog:
        return catalog
    if input_tools:
        return input_tools
    return get_cyt_mcp_catalog(config, blocking=True) or []


def record_verify_proxy_tools(
    *,
    agent: str,
    session_id: str,
    tools: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> None:
    """Append verify-only catalog entries when proxy owns session log writes."""
    if config is None or not verify_only_mode(config):
        return
    if inject_via_for_agent(config, agent) != "proxy":
        return
    if not session_id.strip() or not tools:
        return
    log_path = session_log_path_for_agent(agent, session_id)
    if log_path is None:
        return
    append_verify_session_log(
        log_path,
        tools,
        agent=agent,
        tools_inject_enabled=False,
        hallucination_gate_enabled=True,
        inject_via="proxy",
    )


def record_verify_proxy_turn(
    *,
    agent: str,
    session_id: str,
    user_query: str,
    config: dict[str, Any] | None,
) -> None:
    """Append a verify-only turn entry when proxy owns session log writes."""
    if config is None or not verify_only_mode(config):
        return
    if inject_via_for_agent(config, agent) != "proxy":
        return
    prompt = user_query.strip()
    if not session_id.strip() or not prompt:
        return
    log_path = session_log_path_for_agent(agent, session_id)
    if log_path is None:
        return
    from cyt_client.session_capture import build_turn_entry
    from cyt_client.sessions import append_session_log

    append_session_log(log_path, [build_turn_entry(prompt, "")], agent=agent)


def maybe_record_verify_proxy_request(
    *,
    headers: Mapping[str, str],
    agent: str | None,
    config: dict[str, Any] | None,
    input_tools: list[dict[str, Any]] | None = None,
    original_body: bytes | None = None,
    kind: str | None = None,
    skills_meta: SkillsProxyInjectMeta | None = None,
    pruning: PruneResult | None = None,
) -> None:
    if not agent or config is None or not verify_only_mode(config):
        return
    if inject_via_for_agent(config, agent) != "proxy":
        return
    session_id = session_id_from_headers(headers)
    if not session_id:
        return
    tools = _resolve_verify_tools(config, input_tools)
    if tools:
        record_verify_proxy_tools(
            agent=agent,
            session_id=session_id,
            tools=tools,
            config=config,
        )
    user_query = resolve_proxy_user_query(
        original_body or b"",
        kind,
        skills_meta=skills_meta,
        pruning=pruning,
    )
    if user_query:
        record_verify_proxy_turn(
            agent=agent,
            session_id=session_id,
            user_query=user_query,
            config=config,
        )
