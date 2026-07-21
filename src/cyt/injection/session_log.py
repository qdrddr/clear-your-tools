"""Session JSONL index and per-item injection mode resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from cyt.injection.pre_exposed import is_pre_exposed
from cyt.injection.session_log_build import format_entry_fragment

InjectionMode = Literal["skip", "skinny", "full"]
FULL_PROMOTION_THRESHOLD = 3


@dataclass(frozen=True)
class SessionLogIndex:
    entries: tuple[dict[str, Any], ...]
    agent: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> SessionLogIndex:
        if payload is None:
            return cls(entries=())
        raw = payload.get("cyt_session_log")
        entries: list[dict[str, Any]] = []
        if isinstance(raw, list):
            entries = [
                item for item in raw if isinstance(item, dict) and item.get("type") != "meta"
            ]
        agent_raw = payload.get("cyt_session_agent") or payload.get("cyt_agent")
        agent = agent_raw.strip() if isinstance(agent_raw, str) and agent_raw.strip() else None
        return cls(entries=tuple(entries), agent=agent)

    def verbatim_corpus(self) -> str:
        parts: list[str] = []
        for entry in self.entries:
            fragment = format_entry_fragment(entry)
            if fragment.strip():
                parts.append(fragment.strip())
        return "\n".join(parts)

    def count_key(self, key: str) -> int:
        return sum(1 for entry in self.entries if str(entry.get("key") or "") == key)

    def latest_hash(self, key: str) -> str | None:
        for entry in reversed(self.entries):
            if str(entry.get("key") or "") != key:
                continue
            raw = entry.get("hash")
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return None

    def has_satisfied_full(self, key: str, current_hash: str) -> bool:
        for entry in reversed(self.entries):
            if str(entry.get("key") or "") != key:
                continue
            if not entry.get("full"):
                continue
            logged_hash = entry.get("hash")
            if isinstance(logged_hash, str) and logged_hash.strip() == current_hash:
                return True
        return False


def resolve_injection_mode(
    *,
    key: str,
    current_hash: str,
    index: SessionLogIndex,
    session_text: str,
    formatted_skinny: str,
    formatted_full: str,
) -> InjectionMode:
    corpus = index.verbatim_corpus()
    combined_text = session_text
    if corpus.strip():
        combined_text = f"{session_text}\n{corpus}" if session_text.strip() else corpus

    if index.has_satisfied_full(key, current_hash):
        return "skip"

    for fragment in (formatted_skinny, formatted_full):
        if fragment.strip() and is_pre_exposed(fragment, combined_text):
            return "skip"

    latest = index.latest_hash(key)
    if latest is not None and latest != current_hash:
        return "full"

    if index.count_key(key) >= FULL_PROMOTION_THRESHOLD:
        return "full"

    return "skinny"


def combined_session_text(session_text: str, index: SessionLogIndex) -> str:
    corpus = index.verbatim_corpus()
    if not corpus.strip():
        return session_text
    if not session_text.strip():
        return corpus
    return f"{session_text}\n{corpus}"
