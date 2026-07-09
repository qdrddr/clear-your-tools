"""Hook handler entry point (HTTP server and development CLI)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.config import (
    load_config,
    pruning_pipeline_from_config,
    required_proxy_env_var_names,
    required_pruning_env_var_names,
    required_skills_env_var_names,
    required_tools_hook_env_var_names,
    skills_enabled,
    skills_pipeline,
    tools_enabled,
    tools_inject_via,
)
from cyt.injection.pre_exposed import filter_pre_exposed_skills, filter_pre_exposed_tools
from cyt.injection.session_text import session_text_from_hook_payload
from cyt.proxy.anthropic import PruneResult
from cyt.proxy.user_message_inject import combine_injection_parts
from cyt.skills.agents import resolve_skills_agent
from cyt.skills.budget import (
    count_hook_request_tokens,
    resolve_inject_budget,
    skills_budget_precheck,
    skills_inject_allowed,
)
from cyt.skills.client_skills import build_registry_for_hook_payload
from cyt.skills.debug_log import write_hook_debug_log
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
    hook_transcript_debug_details,
    resolve_model,
    skills_search_query_from_hook_payload,
)
from cyt.tools.budget import (
    resolve_tools_inject_budget,
    tools_budget_precheck,
    tools_inject_allowed,
)
from cyt.tools.hook import finish_tools_hook_injection_from_coordinator, handle_user_prompt_tools
from cyt.tools.inject import format_agent_tools

logger = logging.getLogger(__name__)


def _tools_hook_file_missing(config: dict[str, Any]) -> bool:
    from cyt.config import tools_hook_file_missing

    return tools_hook_file_missing(config)


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
    "skipped_inject_via_proxy": "inject_via is proxy; hook injection skipped",
    "skipped_budget_zero": "skills injection budget is zero for this request",
    "skipped_disabled": "skills.enabled is false",
}


def _ensure_hook_credentials(config: dict[str, Any], *, allow_prompt: bool | None = None) -> None:
    """Load pruner API keys before remote skills/tools search."""
    names: list[str] = []
    if skills_enabled(config):
        names.extend(required_skills_env_var_names(config))
    if tools_inject_via(config) == "hook":
        names.extend(required_pruning_env_var_names(config))
        names.extend(required_tools_hook_env_var_names(config))
    names = list(dict.fromkeys(names))
    if not names:
        return
    from cyt.launch.secrets import ensure_named_credentials

    prompt = allow_prompt if allow_prompt is not None else sys.stdin.isatty()
    ensure_named_credentials(names, allow_prompt=prompt)


def _write_hook_debug_log(
    *,
    debug: bool,
    request_payload: dict[str, Any],
    server_payload: dict[str, Any],
    cwd: str | None,
    config: dict[str, Any],
    cli_prompt: bool,
    outcome: str,
    details: dict[str, Any] | None,
) -> None:
    if not debug:
        return
    from cyt.config import tools_enabled as tools_feature_enabled

    debug_details = _format_hook_debug_details(details)
    write_hook_debug_log(
        request_payload=request_payload,
        server_payload=server_payload,
        cwd=cwd,
        skills_enabled=skills_enabled(config) if not cli_prompt else True,
        tools_enabled=tools_feature_enabled(config),
        outcome=outcome,
        details=debug_details or None,
    )


def _stdout_debug_summary(stdout_text: str) -> dict[str, Any]:
    """Summarize hook stdout for debug logs (what the agent hook actually receives)."""
    if not stdout_text.strip():
        return {"empty": True}
    try:
        parsed = json.loads(stdout_text)
    except json.JSONDecodeError:
        return {"parse_error": True, "stdout_len": len(stdout_text)}
    if not isinstance(parsed, dict):
        return {"parse_error": True, "stdout_len": len(stdout_text)}

    hook_output = parsed.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return {
            "stdout_len": len(stdout_text),
            "top_level_keys": list(parsed.keys()),
            "hook_specific_output": False,
        }

    additional_context = hook_output.get("additionalContext")
    if additional_context is None:
        additional_context = hook_output.get("additional_context")
    context_text = additional_context if isinstance(additional_context, str) else ""
    context_field: str | None
    if "additionalContext" in hook_output:
        context_field = "additionalContext"
    elif "additional_context" in hook_output:
        context_field = "additional_context"
    else:
        context_field = None

    return {
        "stdout_len": len(stdout_text),
        "top_level_keys": list(parsed.keys()),
        "hook_specific_output_keys": list(hook_output.keys()),
        "hook_event_name": hook_output.get("hookEventName"),
        "additional_context_field": context_field,
        "additional_context_len": len(context_text),
        "has_agent_skills": "<agent-skills" in context_text,
        "has_agent_tools": "<agent-tools" in context_text,
        "has_context7": "context7" in context_text.lower(),
    }


_SKILLS_DEBUG_KEYS = frozenset(
    {"pipeline_run", "search_trace", "injected_skills", "injected", "skills_search"},
)
_TOOLS_DEBUG_KEYS = frozenset(
    {
        "prune_status",
        "budget_debug",
        "catalog_tool_count",
        "pruned_tool_count",
        "pruned_tool_names",
        "injected_tools",
    },
)
_RESERVED_DEBUG_KEYS = (
    _SKILLS_DEBUG_KEYS
    | _TOOLS_DEBUG_KEYS
    | frozenset(
        {"stdout", "resolved_model", "request_tokens"},
    )
)


def _skills_debug_block(details: dict[str, Any]) -> dict[str, Any]:
    block: dict[str, Any] = {}
    for key in ("pipeline_run", "search_trace", "injected_skills", "injected"):
        if key not in details or details[key] is None:
            continue
        if key == "injected" and "injected_skills" in details:
            continue
        block[key] = details[key]
    if "skills_search" in details:
        block["search"] = details["skills_search"]
    search_trace = block.pop("search_trace", None)
    if search_trace is not None:
        trace_payload = trace_to_debug_details(search_trace)
        if block.get("injected"):
            trace_payload["injected"] = block["injected"]
        block["search"] = trace_payload
    return block


def _tools_debug_block(details: dict[str, Any]) -> dict[str, Any]:
    block = {key: details[key] for key in _TOOLS_DEBUG_KEYS if key in details}
    if "injected" in details and any(
        key in details for key in ("prune_status", "catalog_tool_count", "pruned_tool_names")
    ):
        block.setdefault("injected", details["injected"])
    return block


def _format_hook_debug_details(details: dict[str, Any] | None) -> dict[str, Any] | None:
    if not details:
        return None

    formatted: dict[str, Any] = {}
    skills_block = _skills_debug_block(details)
    if skills_block:
        formatted["skills"] = skills_block

    tools_block = _tools_debug_block(details)
    if tools_block:
        formatted["tools"] = tools_block

    if "stdout" in details:
        formatted["stdout"] = details["stdout"]
    for key, value in details.items():
        if key not in _RESERVED_DEBUG_KEYS:
            formatted[key] = value
    if "resolved_model" in details:
        formatted["resolved_model"] = details["resolved_model"]
    if "request_tokens" in details:
        formatted["request_tokens"] = details["request_tokens"]

    return formatted or None


def _ensure_skills_credentials(config: dict[str, Any]) -> None:
    """Back-compat alias for hook credential loading."""
    _ensure_hook_credentials(config)


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


@dataclass(frozen=True)
class HookRunResult:
    stdout_text: str
    outcome: str
    details: dict[str, Any] | None = None


def format_hook_stdout(text: str, payload: dict[str, Any], *, plain: bool = False) -> str:
    if not text:
        return ""
    if plain:
        return text
    event_name = hook_event_name(payload)
    if event_name:
        output = {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": text,
            },
        }
        return json.dumps(output)
    return text


def _emit_injection(text: str, payload: dict[str, Any], *, plain: bool = False) -> None:
    formatted = format_hook_stdout(text, payload, plain=plain)
    if formatted:
        print(formatted, flush=True)


def _handle_session_start(_payload: dict[str, Any], _config: dict[str, Any]) -> str:
    from cyt.hook.daemon import daemon_start

    result = daemon_start(verbose=False, unattended=True)
    if result.reused:
        return "session_start_daemon_reused"
    return "session_start_daemon_started"


def _search_skills_for_user_prompt(
    query: str,
    config: dict[str, Any],
    *,
    max_tokens: int,
    plain_output: bool,
    debug: bool,
    io_guarded: bool = False,
    payload: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], SkillsPipelineRun | None, SkillsSearchTrace | None]:
    configure_hook_quiet()
    stdout_guard = contextlib.nullcontext() if plain_output or io_guarded else hook_safe_stdout()
    stderr_guard = contextlib.nullcontext() if plain_output or io_guarded else hook_quiet_stderr()
    with stdout_guard, stderr_guard:
        entries = build_registry_for_hook_payload(
            config,
            payload,
            agent=resolve_skills_agent(),
        )
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


def _handle_user_prompt_skills(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
    debug: bool = False,
    io_guarded: bool = False,
    allow_transcript_file_read: bool = True,
) -> tuple[str, dict[str, Any], str]:
    query = skills_search_query_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    if not query:
        return "user_prompt_missing_prompt", {}, ""

    if not skills_budget_precheck(config):
        return "skipped_budget_zero", {}, ""

    request_tokens = count_hook_request_tokens(payload)
    budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=request_tokens,
    )
    if budget.effective_max <= 0:
        return "skipped_budget_zero", {"request_tokens": request_tokens}, ""

    prompt = prompt_from_payload(payload) or query
    model = resolve_model(payload, allow_file_read=allow_transcript_file_read) or "hook"

    matches, pipeline_run, search_trace = _search_skills_for_user_prompt(
        query,
        config,
        max_tokens=budget.effective_max,
        plain_output=plain_output,
        debug=debug,
        io_guarded=io_guarded,
        payload=payload,
    )
    if not matches:
        outcome, details = _user_prompt_no_matches_outcome(model, pipeline_run, search_trace)
        return outcome, details, ""

    session_text = session_text_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    gated_matches = filter_pre_exposed_skills(matches, session_text)
    injected = format_agent_skills(gated_matches)
    if not injected:
        return (
            "user_prompt_empty_injection",
            {
                "resolved_model": model,
                "search_trace": search_trace,
            },
            "",
        )

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

    return (
        "user_prompt_skills_injected",
        {
            "resolved_model": model,
            "pipeline_run": pipeline_run,
            "search_trace": search_trace,
            "injected_skills": injected if debug else None,
        },
        injected,
    )


def _append_coordinated_skills_injection(
    *,
    payload: dict[str, Any],
    allow_transcript_file_read: bool,
    skill_matches: list[MatchedSkill] | None,
    prompt: str,
    model: str,
    request_tokens: int,
    config: dict[str, Any],
    debug: bool,
    parts: list[str],
    outcomes: list[str],
    details: dict[str, Any],
) -> None:
    if skill_matches is None:
        return
    session_text = session_text_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    gated_matches = filter_pre_exposed_skills(skill_matches, session_text)
    injected_skills = format_agent_skills(gated_matches)
    if injected_skills:
        skills_in = injection_token_count(injected_skills)
        if skills_in > 0:
            record_skills_injection(
                query=prompt,
                model_name=model,
                skills_in=skills_in,
                request_tokens=request_tokens,
                inject_path="hook",
                skills_final_md=injected_skills if debug else None,
                config=config,
            )
        parts.append(injected_skills)
        outcomes.append("user_prompt_skills_injected")
        details["resolved_model"] = model
        if debug:
            details["injected_skills"] = injected_skills
        return
    outcomes.append("user_prompt_no_matches")


def _append_coordinated_tools_injection(
    *,
    payload: dict[str, Any],
    allow_transcript_file_read: bool,
    config: dict[str, Any],
    query: str,
    model: str,
    prune_result: PruneResult,
    catalog: list[dict[str, Any]],
    request_tokens: int,
    budget_debug: dict[str, int],
    debug: bool,
    parts: list[str],
    outcomes: list[str],
    details: dict[str, Any],
) -> None:
    pruned = prune_result.tools or []
    if not pruned:
        outcomes.append("user_prompt_no_tool_matches")
        details["resolved_model"] = model
        details["prune_status"] = prune_result.status
        if debug:
            details["catalog_tool_count"] = len(catalog)
            details["pruned_tool_count"] = 0
        return
    session_text = session_text_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    gated = filter_pre_exposed_tools(pruned, session_text)
    injected_tools = format_agent_tools(gated)
    if not injected_tools:
        outcomes.append("user_prompt_empty_tool_injection")
        return
    tools_outcome, tools_details, _ = finish_tools_hook_injection_from_coordinator(
        payload=payload,
        config=config,
        query=query,
        model=model,
        result=prune_result,
        catalog=catalog,
        injected=injected_tools,
        request_tokens=request_tokens,
        budget_debug=budget_debug,
        debug=debug,
    )
    parts.append(injected_tools)
    outcomes.append(tools_outcome)
    details.update(tools_details)


def _run_coordinated_user_prompt_injection(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    debug: bool,
    skills_allowed: bool,
    tools_allowed: bool,
    allow_transcript_file_read: bool,
    stdio_guarded: bool,
) -> tuple[list[str], list[str], dict[str, Any]]:
    from cyt.pruning.hook_bridge import run_hook_coordinated_prune
    from cyt.skills.hook_quiet import hook_quiet_stderr

    query = skills_search_query_from_hook_payload(
        payload,
        allow_file_read=allow_transcript_file_read,
    )
    if not query:
        return [], ["user_prompt_missing_prompt"], {}

    if _tools_hook_file_missing(config):
        tools_allowed = False
    elif not tools_budget_precheck(config):
        tools_allowed = False

    if not skills_budget_precheck(config):
        skills_allowed = False

    request_tokens = count_hook_request_tokens(payload)
    budget = resolve_inject_budget(
        config,
        "hook",
        total_request_tokens=request_tokens,
    )
    if skills_allowed and budget.effective_max <= 0:
        skills_allowed = False
    budget_max, budget_debug = resolve_tools_inject_budget(
        config,
        total_request_tokens=request_tokens,
    )
    if tools_allowed and budget_max <= 0:
        tools_allowed = False

    if not skills_allowed and not tools_allowed:
        return [], ["skipped_budget_zero"], {"request_tokens": request_tokens}

    prompt = prompt_from_payload(payload) or query
    model = resolve_model(payload, allow_file_read=allow_transcript_file_read) or "hook"

    stdout_guard = hook_safe_stdout(active=stdio_guarded)
    stderr_guard = hook_quiet_stderr(active=stdio_guarded)
    with stdout_guard, stderr_guard:
        prune_result, skill_matches, catalog = run_hook_coordinated_prune(
            query,
            config,
            payload=payload,
            skills_allowed=skills_allowed,
            tools_allowed=tools_allowed,
            skills_max_tokens=budget.effective_max if skills_allowed else None,
            io_guarded=stdio_guarded,
        )

        parts: list[str] = []
        outcomes: list[str] = []
        details: dict[str, Any] = {}

        if skills_allowed:
            _append_coordinated_skills_injection(
                payload=payload,
                allow_transcript_file_read=allow_transcript_file_read,
                skill_matches=skill_matches,
                prompt=prompt,
                model=model,
                request_tokens=request_tokens,
                config=config,
                debug=debug,
                parts=parts,
                outcomes=outcomes,
                details=details,
            )

        if tools_allowed and prune_result is not None and catalog is not None:
            _append_coordinated_tools_injection(
                payload=payload,
                allow_transcript_file_read=allow_transcript_file_read,
                config=config,
                query=query,
                model=model,
                prune_result=prune_result,
                catalog=catalog,
                request_tokens=request_tokens,
                budget_debug=budget_debug,
                debug=debug,
                parts=parts,
                outcomes=outcomes,
                details=details,
            )

        return parts, outcomes, details


def _run_user_prompt_injection(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool,
    debug: bool,
    skills_allowed: bool,
    tools_allowed: bool,
    allow_transcript_file_read: bool,
    io_guarded: bool = False,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Run skills/tools hook injection via the shared coordinator when both are enabled."""
    parts: list[str] = []
    details: dict[str, Any] = {}
    outcomes: list[str] = []

    if (
        skills_allowed
        and tools_allowed
        and not plain_output
        and not _tools_hook_file_missing(config)
    ):
        return _run_coordinated_user_prompt_injection(
            payload,
            config,
            debug=debug,
            skills_allowed=skills_allowed,
            tools_allowed=tools_allowed,
            allow_transcript_file_read=allow_transcript_file_read,
            stdio_guarded=True,
        )

    if skills_allowed:
        skills_outcome, skills_details, skills_text = _handle_user_prompt_skills(
            payload,
            config,
            plain_output=plain_output,
            debug=debug,
            allow_transcript_file_read=allow_transcript_file_read,
        )
        outcomes.append(skills_outcome)
        details.update(skills_details)
        if skills_text:
            parts.append(skills_text)

    if tools_allowed:
        tools_outcome, tools_details, tools_text = handle_user_prompt_tools(
            payload,
            config,
            plain_output=plain_output,
            debug=debug,
            allow_transcript_file_read=allow_transcript_file_read,
        )
        outcomes.append(tools_outcome)
        details.update(tools_details)
        if tools_text:
            parts.append(tools_text)

    return parts, outcomes, details


def _handle_user_prompt(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    plain_output: bool = False,
    debug: bool = False,
    emit_stdout: bool = True,
    allow_transcript_file_read: bool = True,
    io_guarded: bool = False,
) -> tuple[str, dict[str, Any], str]:
    skills_allowed = skills_inject_allowed(config, "hook", cli_prompt=plain_output)
    tools_allowed = tools_inject_allowed(config, "hook", cli_prompt=plain_output)

    parts, outcomes, details = _run_user_prompt_injection(
        payload,
        config,
        plain_output=plain_output,
        debug=debug,
        skills_allowed=skills_allowed,
        tools_allowed=tools_allowed,
        allow_transcript_file_read=allow_transcript_file_read,
        io_guarded=io_guarded,
    )

    if debug:
        details.update(
            hook_transcript_debug_details(
                payload,
                allow_file_read=allow_transcript_file_read,
            ),
        )

    combined = combine_injection_parts(parts)
    if combined:
        if emit_stdout:
            _emit_injection(combined, payload, plain=plain_output)
        outcome = "user_prompt_injected"
    elif outcomes:
        outcome = outcomes[-1]
    else:
        outcome = "skipped_inject_via_proxy"

    if combined:
        return outcome, details, combined
    if outcomes:
        return outcome, details, ""
    return outcome, details, ""


def _cli_prompt_payload(prompt: str, model: str | None) -> tuple[str, dict[str, Any]]:
    import uuid

    payload: dict[str, Any] = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt.strip(),
        "cwd": os.environ.get("CYT_HOOK_CWD") or str(Path.cwd()),
        "session_id": os.environ.get("CYT_SESSION_ID") or f"cli-{uuid.uuid4().hex[:12]}",
    }
    if model:
        payload["model"] = model
    agent = os.environ.get("CYT_LAUNCH_AGENT", "").strip()
    if agent:
        payload["cyt_agent"] = agent
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


def _exit_if_hook_disabled(
    *,
    config: dict[str, Any],
    cli_prompt: bool,
    debug: bool,
    raw_stdin: str,
    payload: dict[str, Any],
    cwd: str | None,
) -> bool:
    del debug, raw_stdin, payload, cwd
    if cli_prompt or skills_enabled(config) or tools_enabled(config):
        return False
    print(
        "cyt hook: skills.enabled and pruning.tools.enabled are both false; "
        "hook produced no injection.",
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
    emit_stdout: bool = True,
    allow_transcript_file_read: bool = True,
    io_guarded: bool = False,
) -> tuple[str, dict[str, Any] | None, str]:
    outcome = "empty_stdin" if not raw_stdin.strip() else "noop"
    details: dict[str, Any] | None = None
    injection_text = ""

    if event_name in _SESSION_EVENTS:
        outcome = _handle_session_start(payload, config)
        if debug:
            details = {
                "session_id": session_id(payload),
                "model": model_from_payload(payload),
            }
    elif event_name in _PROMPT_EVENTS:
        skills_allowed = skills_inject_allowed(config, "hook", cli_prompt=cli_prompt)
        tools_allowed = tools_inject_allowed(config, "hook", cli_prompt=cli_prompt)
        if not skills_allowed and not tools_allowed:
            outcome = "skipped_inject_via_proxy"
        else:
            outcome, details, injection_text = _handle_user_prompt(
                payload,
                config,
                plain_output=cli_prompt,
                debug=debug,
                emit_stdout=emit_stdout,
                allow_transcript_file_read=allow_transcript_file_read,
                io_guarded=io_guarded,
            )
    elif event_name is not None:
        outcome = "unhandled_event"
    elif raw_stdin.strip():
        outcome = "missing_hook_event_name"

    return outcome, details, injection_text


def run_hook_payload(
    payload: dict[str, Any],
    config: dict[str, Any],
    *,
    request_payload: dict[str, Any] | None = None,
    plain_output: bool = False,
    debug: bool = False,
    io_guarded: bool = False,
    allow_transcript_file_read: bool = False,
) -> HookRunResult:
    """Run hook logic for *payload* and return formatted stdout without printing."""
    configure_hook_quiet()
    _ensure_hook_credentials(config, allow_prompt=False)
    captured_request = request_payload if request_payload is not None else payload
    raw_stdin = json.dumps(captured_request)
    event_name = hook_event_name(payload)
    outcome, details, injection_text = _dispatch_hook_event(
        event_name,
        payload,
        config,
        raw_stdin,
        cli_prompt=plain_output,
        debug=debug,
        emit_stdout=False,
        allow_transcript_file_read=allow_transcript_file_read,
        io_guarded=io_guarded,
    )
    stdout_text = format_hook_stdout(injection_text, payload, plain=plain_output)
    debug_details = details
    if debug and details is not None:
        debug_details = dict(details)
        debug_details["stdout"] = _stdout_debug_summary(stdout_text)
    _write_hook_debug_log(
        debug=debug,
        request_payload=captured_request,
        server_payload=payload,
        cwd=hook_cwd(payload),
        config=config,
        cli_prompt=plain_output,
        outcome=outcome,
        details=debug_details,
    )
    return HookRunResult(stdout_text=stdout_text, outcome=outcome, details=details)


def _request_payload_from_raw_stdin(raw_stdin: str, *, fallback: dict[str, Any]) -> dict[str, Any]:
    if not raw_stdin.strip():
        return fallback
    try:
        parsed = json.loads(raw_stdin)
    except json.JSONDecodeError:
        return fallback
    return parsed if isinstance(parsed, dict) else fallback


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

    if cli_prompt:
        print(
            f"skills.pipeline (configured): {skills_pipeline(config)}",
            file=sys.stderr,
        )

    if _exit_if_hook_disabled(
        config=config,
        cli_prompt=cli_prompt,
        debug=debug,
        raw_stdin=raw_stdin,
        payload=payload,
        cwd=cwd,
    ):
        return

    _ensure_hook_credentials(config)

    event_name = hook_event_name(payload)
    outcome, details, _injection_text = _dispatch_hook_event(
        event_name,
        payload,
        config,
        raw_stdin,
        cli_prompt=cli_prompt,
        debug=debug,
        emit_stdout=True,
        allow_transcript_file_read=True,
    )

    if cli_prompt and outcome != "user_prompt_injected":
        if not (details and details.get("pipeline_run")):
            print("skills.pipeline (executed): (not run)", file=sys.stderr)
        _report_cli_outcome(outcome)

    if debug:
        _write_hook_debug_log(
            debug=debug,
            request_payload=_request_payload_from_raw_stdin(raw_stdin, fallback=payload),
            server_payload=payload,
            cwd=cwd,
            config=config,
            cli_prompt=cli_prompt,
            outcome=outcome,
            details=details,
        )


def _strip_dev_cli_hook_command(argv: list[str]) -> list[str]:
    if argv and argv[0] == "hook":
        return argv[1:]
    return argv


def main() -> None:
    """Development entry point for ``python src/cyt/skills/cli.py [--stdin] [--test]``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="CYT hook handler (development entry point; agents use cyt-client)",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read hook JSON from stdin (session tracking and skill injection)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log hook diagnostics to .debug/hooks/ when used with --prompt",
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
