"""Shared pre-exposure context for hook and proxy injection paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from cyt.injection.session_log import SessionLogIndex, combined_session_text
from cyt.injection.session_text import (
    session_text_from_hook_payload,
    session_text_from_proxy_body,
)
from cyt_client.sessions import (
    entries_after_latest_compaction,
    index_of_latest_compaction,
    read_session_log_file,
)

ProxyKind = Literal["anthropic", "openai"]


@dataclass(frozen=True)
class PreExposureContext:
    payload_text: str
    index: SessionLogIndex
    combined_text: str
    had_compaction: bool

    @classmethod
    def from_entries(
        cls,
        *,
        payload_text: str,
        entries: list[dict[str, Any]],
        agent: str | None = None,
    ) -> PreExposureContext:
        had_compaction = index_of_latest_compaction(entries) is not None
        sliced = entries_after_latest_compaction(entries)
        index = SessionLogIndex(entries=tuple(sliced), agent=agent)
        combined = combined_session_text(payload_text, index)
        return cls(
            payload_text=payload_text,
            index=index,
            combined_text=combined,
            had_compaction=had_compaction,
        )

    @classmethod
    def for_hook_payload(
        cls,
        payload: dict[str, Any],
        *,
        allow_file_read: bool = True,
    ) -> PreExposureContext:
        entries, agent = _hook_session_entries(payload)
        had_compaction = index_of_latest_compaction(entries) is not None
        payload_parts: list[str] = []
        prompt = _hook_prompt_text(payload)
        if prompt:
            payload_parts.append(prompt)
        turn_corpus = _post_compaction_turn_corpus(entries)
        if turn_corpus.strip():
            payload_parts.append(turn_corpus)
        if not had_compaction:
            transcript_text = _hook_transcript_text(
                payload,
                allow_file_read=allow_file_read,
            )
            if transcript_text:
                payload_parts.append(transcript_text)

        payload_text = "\n".join(part for part in payload_parts if part.strip())
        return cls.from_entries(payload_text=payload_text, entries=entries, agent=agent)

    @classmethod
    def for_proxy(
        cls,
        body: dict[str, Any],
        kind: ProxyKind,
        *,
        agent: str,
        session_id: str | None,
    ) -> PreExposureContext:
        payload_text = session_text_from_proxy_body(body, kind)
        entries: list[dict[str, Any]] = []
        log_agent: str | None = agent
        if session_id and session_id.strip():
            from cyt.proxy.verify_session_log import session_log_path_for_agent

            path = session_log_path_for_agent(agent, session_id.strip())
            if path is not None and path.is_file():
                log_agent, entries = read_session_log_file(path)
        return cls.from_entries(
            payload_text=payload_text,
            entries=entries,
            agent=log_agent or agent,
        )


def _rules_injection_from_payload(payload: dict[str, Any]) -> str:
    raw = payload.get("cyt_rules_injection")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _post_compaction_turn_corpus(entries: list[dict[str, Any]]) -> str:
    sliced = entries_after_latest_compaction(entries)
    parts: list[str] = []
    for entry in sliced:
        if entry.get("kind") != "turn":
            continue
        prompt = str(entry.get("prompt") or "").strip()
        assistant = str(entry.get("assistant") or "").strip()
        if prompt:
            parts.append(prompt)
        if assistant:
            parts.append(assistant)
    return "\n".join(parts)


def _session_log_path_for_payload(payload: dict[str, Any]) -> Path | None:
    from cyt_client.sessions import session_log_path

    return session_log_path(payload)


def _hook_session_entries(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    path = _session_log_path_for_payload(payload)
    entries: list[dict[str, Any]] = []
    agent: str | None = None
    if path is not None and path.is_file():
        agent, entries = read_session_log_file(path)
    elif isinstance(payload.get("cyt_session_log"), list):
        entries = [
            item
            for item in payload["cyt_session_log"]
            if isinstance(item, dict) and item.get("type") != "meta"
        ]
        agent_raw = payload.get("cyt_session_agent") or payload.get("cyt_agent")
        if isinstance(agent_raw, str) and agent_raw.strip():
            agent = agent_raw.strip()
    return entries, agent


def _hook_prompt_text(payload: dict[str, Any]) -> str:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        prompt = layer.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            return prompt.strip()
    return ""


def _hook_transcript_text(
    payload: dict[str, Any],
    *,
    allow_file_read: bool,
) -> str:
    transcript_text = session_text_from_hook_payload(
        payload,
        allow_file_read=allow_file_read,
    )
    if not transcript_text.strip():
        return ""
    rules = _rules_injection_from_payload(payload)
    if rules and rules in transcript_text:
        transcript_text = transcript_text.replace(rules, "").strip()
    return transcript_text.strip()
