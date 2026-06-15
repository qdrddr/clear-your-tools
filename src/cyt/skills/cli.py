"""`cyt hook --stdin` agent hook entry point."""

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
    required_proxy_env_var_names,
    required_pruning_env_var_names,
    required_skills_env_var_names,
    skills_enabled,
    skills_pipeline,
)
from cyt.skills.agents import resolve_skills_agent
from cyt.skills.budget import (
    count_hook_request_tokens,
    resolve_inject_budget,
    skills_budget_precheck,
    skills_inject_allowed,
)
from cyt.skills.catalog import build_registry
from cyt.skills.debug_log import write_skills_hook_debug_log
from cyt.skills.diagnostics import SkillsSearchTrace
from cyt.skills.hook_payload import (
    hook_cwd,
    hook_event_name,
    model_from_payload,
    normalize_hook_payload,
    prompt_from_payload,
    session_id,
)
from cyt.skills.hook_quiet import configure_hook_quiet, hook_quiet_stderr, hook_safe_stdout
from cyt.skills.inject import format_agent_skills, injection_token_count
from cyt.skills.search import (
    MatchedSkill,
    SkillsPipelineRun,
    search_skills,
    search_skills_with_trace,
)
from cyt.skills.stats import record_skills_injection
from cyt.skills.trace import print_skills_search_trace, trace_to_debug_details
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
    "user_prompt_budget_exceeded": (
        "matched skills exceed the injection token budget "
        "(see skills.inject budget above; raise skills.max_tokens_per_request or hook.request_budget_fraction)"
    ),
    "user_prompt_empty_injection": "matched chunks produced empty injection text",
    "user_prompt_missing_prompt": "missing or empty prompt",
    "skipped_inject_via_proxy": "skills.inject_via is proxy; hook injection skipped",
    "skipped_budget_zero": "skills injection budget is zero for this request",
    "skipped_disabled": "skills.enabled is false",
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


def _print_skills_pipeline_run(pipeline_run: SkillsPipelineRun) -> None:
    if pipeline_run.executed:
        print(f"skills.pipeline (executed): {pipeline_run.executed}", file=sys.stderr)
    else:
        print("skills.pipeline (executed): (not run)", file=sys.stderr)
    if pipeline_run.fallback_reason:
        print(f"skills.pipeline fallback: {pipeline_run.fallback_reason}", file=sys.stderr)


def _print_skills_test_report(config: dict[str, Any]) -> None:
    """Print skills/pruning pipeline settings and required API key resolution."""
    enabled = skills_enabled(config)
    pipeline = skills_pipeline(config)
    pruning_pipeline = pruning_pipeline_from_config(config)
    print(f"skills.enabled: {enabled}")
    print(f"skills.pipeline (configured): {pipeline}")
    print(f"pruning.pipeline (configured): {pruning_pipeline}")

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
    _print_required_api_keys(
        "All required API keys",
        required_proxy_env_var_names(config),
        empty_hint="none required for current pipelines",
    )


def _report_cli_outcome(outcome: str) -> None:
    hint = _CLI_OUTCOME_HINTS.get(outcome)
    if hint:
        print(f"cyt hook: {hint}", file=sys.stderr)


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


def _search_skills_for_user_prompt(
    query: str,
    config: dict[str, Any],
    *,
    max_tokens: int,
    plain_output: bool,
    debug: bool,
) -> tuple[list[MatchedSkill], SkillsPipelineRun | None, SkillsSearchTrace | None]:
    configure_hook_quiet()
    stdout_guard = contextlib.nullcontext() if plain_output else hook_safe_stdout()
    stderr_guard = contextlib.nullcontext() if plain_output else hook_quiet_stderr()
    with stdout_guard, stderr_guard:
        entries = build_registry(config, agent=resolve_skills_agent())
        if plain_output:
            matches, search_trace = search_skills_with_trace(
                query,
                entries,
                config=config,
                max_tokens=max_tokens,
            )
            pipeline_run = search_trace.pipeline_run
        else:
            matches = search_skills(
                query,
                entries,
                config=config,
                max_tokens=max_tokens,
            )
            pipeline_run = None
            search_trace = None
    if plain_output and pipeline_run is not None:
        _print_skills_pipeline_run(pipeline_run)
    if plain_output and search_trace is not None:
        print_skills_search_trace(search_trace, debug=debug)
    return matches, pipeline_run, search_trace


def _user_prompt_no_matches_outcome(
    model: str,
    pipeline_run: SkillsPipelineRun | None,
    search_trace: SkillsSearchTrace | None,
) -> tuple[str, dict[str, Any]]:
    passed_search = any(row.passed for row in (search_trace.search_rows if search_trace else []))
    if search_trace and (search_trace.pre_budget_matches or passed_search):
        outcome = "user_prompt_budget_exceeded"
    else:
        outcome = "user_prompt_no_matches"
    return outcome, {
        "resolved_model": model,
        "pipeline_run": pipeline_run,
        "search_trace": search_trace,
    }


def _handle_user_prompt(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
    debug: bool = False,
) -> tuple[str, dict[str, Any]]:
    if not skills_inject_allowed(config, "hook", cli_prompt=plain_output):
        return "skipped_inject_via_proxy", {}

    query = skills_search_query_from_hook_payload(payload)
    if not query:
        return "user_prompt_missing_prompt", {}

    if not skills_budget_precheck(config):
        return "skipped_budget_zero", {}

    request_tokens = count_hook_request_tokens(payload)
    budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=request_tokens,
    )
    if budget.effective_max <= 0:
        return "skipped_budget_zero", {"request_tokens": request_tokens}

    prompt = prompt_from_payload(payload) or query
    model = _resolve_model(payload) or "hook"

    matches, pipeline_run, search_trace = _search_skills_for_user_prompt(
        query,
        config,
        max_tokens=budget.effective_max,
        plain_output=plain_output,
        debug=debug,
    )
    if not matches:
        return _user_prompt_no_matches_outcome(model, pipeline_run, search_trace)

    injected = format_agent_skills(matches)
    if not injected:
        return "user_prompt_empty_injection", {
            "resolved_model": model,
            "search_trace": search_trace,
        }

    skills_in = injection_token_count(injected)
    if skills_in > 0:
        record_skills_injection(
            query=prompt,
            model_name=model,
            skills_in=skills_in,
            request_tokens=request_tokens,
            inject_path="hook",
            skills_final_md=injected if debug else None,
            config=config,
        )

    _emit_injection(injected, payload, plain=plain_output)
    return "user_prompt_injected", {
        "resolved_model": model,
        "pipeline_run": pipeline_run,
        "search_trace": search_trace,
        "injected": injected if debug else None,
    }


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
        "cyt hook: skills.enabled is false in config; hook produced no injection. "
        "Set skills.enabled: true in ~/.config/cyt/config.yaml",
        file=sys.stderr,
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
        if not skills_inject_allowed(config, "hook", cli_prompt=cli_prompt):
            outcome = "skipped_inject_via_proxy"
        else:
            outcome, details = _handle_user_prompt(
                payload,
                config,
                plain_output=cli_prompt,
                debug=debug,
            )
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

    if cli_prompt:
        print(
            f"skills.pipeline (configured): {skills_pipeline(config)}",
            file=sys.stderr,
        )

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
        if not (details and details.get("pipeline_run")):
            print("skills.pipeline (executed): (not run)", file=sys.stderr)
        _report_cli_outcome(outcome)

    if debug and cli_prompt:
        debug_details = dict(details) if details else {}
        search_trace = debug_details.pop("search_trace", None)
        if search_trace is not None:
            trace_payload = trace_to_debug_details(search_trace)
            if debug_details.get("injected"):
                trace_payload["injected"] = debug_details["injected"]
            debug_details["skills_search"] = trace_payload
        write_skills_hook_debug_log(
            raw_stdin=raw_stdin,
            payload=payload,
            cwd=cwd,
            skills_enabled=enabled if not cli_prompt else True,
            outcome=outcome,
            details=debug_details or None,
        )


def _strip_dev_cli_hook_command(argv: list[str]) -> list[str]:
    if argv and argv[0] == "hook":
        return argv[1:]
    return argv


def main() -> None:
    """Development entry point for ``python src/cyt/skills/cli.py [--stdin] [--test]``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="CYT hook handler (development entry point; prefer `cyt hook`)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read hook JSON from stdin (session tracking and skill injection)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log hook diagnostics to .debug/skills/ when used with --prompt",
    )
    parser.add_argument(
        "--prompt",
        metavar="TEXT",
        default=None,
        help="Run skill search/injection for TEXT (terminal mode; skips stdin)",
    )
    parser.add_argument(
        "--model",
        metavar="MODEL",
        default=None,
        help="Model name for stats when using --prompt (optional)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Print skills/pruning pipelines and required API key resolution (no hook I/O)",
    )
    args = parser.parse_args(_strip_dev_cli_hook_command(sys.argv[1:]))

    if not (args.stdin or args.prompt or args.test):
        parser.error("one of --stdin, --prompt, or --test is required")

    run(
        debug=bool(args.debug),
        prompt=args.prompt,
        model=args.model,
        test=bool(args.test),
    )


if __name__ == "__main__":
    main()
