"""`cyt skills` agent hook entry point."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from cyt.config import load_config, skills_enabled
from cyt.skills.cache import SessionCacheDB
from cyt.skills.catalog import build_registry
from cyt.skills.debug_log import write_skills_hook_debug_log
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.search import search_skills
from cyt.skills.stats import record_skills_injection

logger = logging.getLogger(__name__)

_SESSION_EVENTS = frozenset({"SessionStart"})
_PROMPT_EVENTS = frozenset({"UserPromptSubmit"})

_CLI_OUTCOME_HINTS: dict[str, str] = {
    "user_prompt_no_matches": "no skill chunks matched this prompt (check skills.directories in config)",
    "user_prompt_empty_injection": "matched chunks produced empty injection text",
    "user_prompt_missing_prompt": "missing or empty prompt",
}


def _report_cli_outcome(outcome: str) -> None:
    hint = _CLI_OUTCOME_HINTS.get(outcome)
    if hint:
        print(f"cyt skills: {hint}", file=sys.stderr)


def _read_hook_payload() -> tuple[str, dict[str, Any]]:
    raw = sys.stdin.read()
    if not raw.strip():
        return raw, {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("skills hook received non-JSON stdin")
        return raw, {}
    if isinstance(payload, dict):
        return raw, payload
    return raw, {}


def _hook_event_name(payload: dict[str, Any]) -> str | None:
    name = payload.get("hook_event_name") or payload.get("hookEventName")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _hook_cwd(payload: dict[str, Any]) -> str | None:
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def _session_id(payload: dict[str, Any]) -> str | None:
    session_id = payload.get("session_id") or payload.get("sessionId")
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return None


def _model_from_payload(payload: dict[str, Any]) -> str | None:
    model = payload.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _resolve_model(payload: dict[str, Any], config: dict[str, Any]) -> str | None:
    """Session cache (Claude SessionStart) first; Codex includes model on UserPromptSubmit."""
    session_id = _session_id(payload)
    if session_id:
        cache = SessionCacheDB.open(config)
        try:
            cached = cache.lookup_model(session_id)
        finally:
            cache.close()
        if cached:
            return cached
    return _model_from_payload(payload)


def _register_session_if_possible(payload: dict[str, Any], config: dict[str, Any]) -> None:
    session_id = _session_id(payload)
    model = _model_from_payload(payload)
    if not session_id or not model:
        return
    cache = SessionCacheDB.open(config)
    try:
        cache.upsert_session(session_id, model)
    finally:
        cache.close()


def _emit_injection(text: str, payload: dict[str, Any], *, plain: bool = False) -> None:
    if plain:
        print(text)
        return
    event_name = _hook_event_name(payload)
    if event_name:
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


def _handle_session_start(payload: dict[str, Any], config: dict[str, Any]) -> str:
    session_id = _session_id(payload)
    model = _model_from_payload(payload)
    if not session_id or not model:
        return "session_start_missing_fields"
    cache = SessionCacheDB.open(config)
    try:
        cache.purge_stale()
        cache.upsert_session(session_id, model)
    finally:
        cache.close()
    return "session_start_registered"


def _handle_user_prompt(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
) -> str:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return "user_prompt_missing_prompt"

    model = _resolve_model(payload, config)
    _register_session_if_possible(payload, config)

    entries = build_registry(config)
    matches = search_skills(prompt, entries, config=config)
    if not matches:
        return "user_prompt_no_matches"

    injected = format_agent_skills(matches)
    if not injected:
        return "user_prompt_empty_injection"

    skills_in = injection_token_count(injected)
    if model and skills_in > 0:
        record_skills_injection(
            query=prompt,
            model_name=model,
            skills_in=skills_in,
            config=config,
        )

    _emit_injection(injected, payload, plain=plain_output)
    return "user_prompt_injected"


def _cli_prompt_payload(prompt: str, model: str | None) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt.strip(),
        "cwd": str(Path.cwd()),
    }
    if model:
        payload["model"] = model
    return json.dumps(payload), payload


def run(
    debug: bool = False,
    prompt: str | None = None,
    model: str | None = None,
) -> None:
    config = load_config()
    cli_prompt_text = prompt.strip() if prompt else ""
    cli_prompt = bool(cli_prompt_text)
    if cli_prompt:
        raw_stdin, payload = _cli_prompt_payload(cli_prompt_text, model)
    else:
        raw_stdin, payload = _read_hook_payload()
    cwd = _hook_cwd(payload)
    enabled = skills_enabled(config)
    outcome = "empty_stdin" if not raw_stdin.strip() else "noop"

    cache = SessionCacheDB.open(config)
    try:
        cache.purge_stale()
    finally:
        cache.close()

    if not enabled and not cli_prompt:
        print(
            "cyt skills: skills.enabled is false in config; hook produced no injection. "
            "Set skills.enabled: true in ~/.config/cyt/config.yaml",
            file=sys.stderr,
        )
        if debug:
            write_skills_hook_debug_log(
                raw_stdin=raw_stdin,
                payload=payload,
                cwd=cwd,
                skills_enabled=False,
                outcome="skipped_disabled",
            )
        return

    event_name = _hook_event_name(payload)
    if event_name in _SESSION_EVENTS:
        outcome = _handle_session_start(payload, config)
    elif event_name in _PROMPT_EVENTS:
        outcome = _handle_user_prompt(payload, config, plain_output=cli_prompt)
    elif event_name is not None:
        outcome = "unhandled_event"
    elif raw_stdin.strip():
        outcome = "missing_hook_event_name"

    if cli_prompt and outcome != "user_prompt_injected":
        _report_cli_outcome(outcome)

    if debug:
        write_skills_hook_debug_log(
            raw_stdin=raw_stdin,
            payload=payload,
            cwd=cwd,
            skills_enabled=enabled if not cli_prompt else True,
            outcome=outcome,
        )
