"""`cyt skills` agent hook entry point."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    pruning_pipeline_from_config,
    required_pruning_env_var_names,
    required_skills_env_var_names,
    skills_enabled,
    skills_pipeline,
)
from cyt.skills.catalog import build_registry
from cyt.skills.debug_log import write_skills_hook_debug_log
from cyt.skills.hook_payload import (
    hook_cwd,
    hook_event_name,
    model_from_payload,
    normalize_hook_payload,
    prompt_from_payload,
    session_id,
)
from cyt.skills.hook_quiet import configure_hook_quiet, hook_safe_stdout
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.proxy_inject import skills_inject_via_hook
from cyt.skills.search import search_skills
from cyt.skills.stats import record_skills_injection
from cyt.skills.transcript import (
    model_from_transcript,
    skills_search_query_from_hook_payload,
    transcript_path_from_payload,
)

logger = logging.getLogger(__name__)

_SESSION_EVENTS = frozenset({"SessionStart"})
_PROMPT_EVENTS = frozenset({"UserPromptSubmit"})

_CLI_OUTCOME_HINTS: dict[str, str] = {
    "user_prompt_no_matches": "no skill chunks matched this prompt (check skills.directories in config)",
    "user_prompt_empty_injection": "matched chunks produced empty injection text",
    "user_prompt_missing_prompt": "missing or empty prompt",
}


def _ensure_skills_credentials(config: dict[str, Any]) -> None:
    """Load skills-pruner API keys (keyring, .env, shell) before remote search."""
    if not skills_enabled(config):
        return
    names = required_skills_env_var_names(config)
    if not names:
        return
    from cyt.launch.secrets import ensure_named_credentials

    ensure_named_credentials(names, allow_prompt=False)


def _print_required_api_keys(label: str, names: list[str], *, empty_hint: str) -> None:
    from cyt.launch.secrets import inspect_named_credentials

    if not names:
        print(f"{label}: (none — {empty_hint})")
        return

    print(f"{label}:")
    for name, source in inspect_named_credentials(names):
        if source:
            print(f"  {name}: {source}")
        else:
            print(f"  {name}: missing")


def _print_skills_test_report(config: dict[str, Any]) -> None:
    """Print skills/pruning pipeline settings and required API key resolution."""
    enabled = skills_enabled(config)
    pipeline = skills_pipeline(config)
    pruning_pipeline = pruning_pipeline_from_config(config)
    print(f"skills.enabled: {enabled}")
    print(f"skills.pipeline: {pipeline}")
    print(f"pruning.pipeline: {pruning_pipeline}")

    if not enabled:
        _print_required_api_keys(
            "Skills API keys",
            [],
            empty_hint="skills disabled",
        )
    else:
        _print_required_api_keys(
            "Skills API keys",
            required_skills_env_var_names(config),
            empty_hint="BM25 pipeline needs no remote keys",
        )

    _print_required_api_keys(
        "Pruning API keys",
        required_pruning_env_var_names(config),
        empty_hint="BM25 pipeline needs no remote keys",
    )


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
        return raw, normalize_hook_payload(payload)
    return raw, {}


def _resolve_model(payload: dict[str, Any]) -> str | None:
    """Codex/CLI: payload.model; Claude Code: transcript_path jsonl message.model."""
    direct = model_from_payload(payload)
    if direct:
        return direct
    path = transcript_path_from_payload(payload)
    if path:
        return model_from_transcript(path)
    return None


def _emit_injection(text: str, payload: dict[str, Any], *, plain: bool = False) -> None:
    if plain:
        print(text)
        return
    event_name = hook_event_name(payload)
    if event_name:
        output = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            },
        }
        print(json.dumps(output), flush=True)
        return
    if text:
        print(text, flush=True)


def _handle_session_start(_payload: dict[str, Any], _config: dict[str, Any]) -> str:
    """Back-compat: SessionStart hooks are ignored (no session cache)."""
    return "session_start_ignored"


def _handle_user_prompt(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
) -> tuple[str, dict[str, Any]]:
    query = skills_search_query_from_hook_payload(payload)
    if not query:
        return "user_prompt_missing_prompt", {}

    prompt = prompt_from_payload(payload) or query
    model = _resolve_model(payload)

    configure_hook_quiet()
    stdout_guard = contextlib.nullcontext() if plain_output else hook_safe_stdout()
    with stdout_guard:
        entries = build_registry(config)
        matches = search_skills(query, entries, config=config)
    if not matches:
        return "user_prompt_no_matches", {"resolved_model": model}

    injected = format_agent_skills(matches)
    if not injected:
        return "user_prompt_empty_injection", {"resolved_model": model}

    skills_in = injection_token_count(injected)
    if model and skills_in > 0:
        record_skills_injection(
            query=prompt,
            model_name=model,
            skills_in=skills_in,
            config=config,
        )

    _emit_injection(injected, payload, plain=plain_output)
    return "user_prompt_injected", {"resolved_model": model}


def _cli_prompt_payload(prompt: str, model: str | None) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt.strip(),
        "cwd": str(Path.cwd()),
    }
    if model:
        payload["model"] = model
    return json.dumps(payload), payload


def _read_run_input(
    prompt: str | None,
    model: str | None,
) -> tuple[str, dict[str, Any], bool]:
    cli_prompt_text = prompt.strip() if prompt else ""
    cli_prompt = bool(cli_prompt_text)
    if cli_prompt:
        raw_stdin, payload = _cli_prompt_payload(cli_prompt_text, model)
    else:
        raw_stdin, payload = _read_hook_payload()
    return raw_stdin, payload, cli_prompt


def _exit_if_skills_disabled(
    *,
    enabled: bool,
    cli_prompt: bool,
    debug: bool,
    raw_stdin: str,
    payload: dict[str, Any],
    cwd: str | None,
) -> bool:
    if enabled or cli_prompt:
        return False
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
    return True


def _dispatch_hook_event(
    event_name: str | None,
    payload: dict[str, Any],
    config: dict[str, Any],
    raw_stdin: str,
    *,
    cli_prompt: bool,
    debug: bool,
) -> tuple[str, dict[str, Any] | None]:
    outcome = "empty_stdin" if not raw_stdin.strip() else "noop"
    details: dict[str, Any] | None = None

    if event_name in _SESSION_EVENTS:
        outcome = _handle_session_start(payload, config)
        if debug:
            details = {
                "session_id": session_id(payload),
                "model": model_from_payload(payload),
            }
    elif event_name in _PROMPT_EVENTS:
        if not cli_prompt and not skills_inject_via_hook(config):
            outcome = "skipped_inject_via_proxy"
        else:
            outcome, details = _handle_user_prompt(payload, config, plain_output=cli_prompt)
    elif event_name is not None:
        outcome = "unhandled_event"
    elif raw_stdin.strip():
        outcome = "missing_hook_event_name"

    return outcome, details


def run(
    debug: bool = False,
    prompt: str | None = None,
    model: str | None = None,
    test: bool = False,
) -> None:
    configure_hook_quiet()
    config = load_config()
    if test:
        _print_skills_test_report(config)
        return

    raw_stdin, payload, cli_prompt = _read_run_input(prompt, model)
    cwd = hook_cwd(payload)
    enabled = skills_enabled(config)

    if _exit_if_skills_disabled(
        enabled=enabled,
        cli_prompt=cli_prompt,
        debug=debug,
        raw_stdin=raw_stdin,
        payload=payload,
        cwd=cwd,
    ):
        return

    _ensure_skills_credentials(config)

    event_name = hook_event_name(payload)
    outcome, details = _dispatch_hook_event(
        event_name,
        payload,
        config,
        raw_stdin,
        cli_prompt=cli_prompt,
        debug=debug,
    )

    if cli_prompt and outcome != "user_prompt_injected":
        _report_cli_outcome(outcome)

    if debug:
        write_skills_hook_debug_log(
            raw_stdin=raw_stdin,
            payload=payload,
            cwd=cwd,
            skills_enabled=enabled if not cli_prompt else True,
            outcome=outcome,
            details=details,
        )
