"""`cyt skills` agent hook entry point."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from cyt.config import load_config, skills_enabled
from cyt.skills.cache import SessionCacheDB
from cyt.skills.catalog import build_registry
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.search import search_skills
from cyt.skills.stats import record_skills_injection

logger = logging.getLogger(__name__)

_SESSION_EVENTS = frozenset({"SessionStart"})
_PROMPT_EVENTS = frozenset({"UserPromptSubmit"})


def _read_hook_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("skills hook received non-JSON stdin")
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_hook_output(text: str, payload: dict[str, Any]) -> None:
    event_name = payload.get("hook_event_name")
    if isinstance(event_name, str) and event_name:
        output = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            },
        }
        print(json.dumps(output))
        return
    if text:
        print(text)


def _handle_session_start(payload: dict[str, Any], config: dict[str, Any]) -> None:
    session_id = payload.get("session_id") or payload.get("sessionId")
    model = payload.get("model")
    if not session_id or not model:
        return
    cache = SessionCacheDB.open(config)
    try:
        cache.purge_stale()
        cache.upsert_session(str(session_id), str(model))
    finally:
        cache.close()


def _handle_user_prompt(payload: dict[str, Any], config: dict[str, Any]) -> None:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return

    session_id = payload.get("session_id") or payload.get("sessionId")
    model: str | None = None
    if session_id:
        cache = SessionCacheDB.open(config)
        try:
            model = cache.lookup_model(str(session_id))
        finally:
            cache.close()

    entries = build_registry(config)
    matches = search_skills(prompt, entries, config=config)
    if not matches:
        return

    injected = format_agent_skills(matches)
    if not injected:
        return

    skills_in = injection_token_count(injected)
    if model and skills_in > 0:
        record_skills_injection(
            query=prompt,
            model_name=model,
            skills_in=skills_in,
            config=config,
        )

    _write_hook_output(injected, payload)


def run() -> None:
    config = load_config()
    payload = _read_hook_payload()

    cache = SessionCacheDB.open(config)
    try:
        cache.purge_stale()
    finally:
        cache.close()

    if not skills_enabled(config):
        return

    event_name = payload.get("hook_event_name")
    if isinstance(event_name, str) and event_name in _SESSION_EVENTS:
        _handle_session_start(payload, config)
        return

    if isinstance(event_name, str) and event_name in _PROMPT_EVENTS:
        _handle_user_prompt(payload, config)
        return
