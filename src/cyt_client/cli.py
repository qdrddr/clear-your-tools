"""``cyt-client`` entry point: stdin JSON → POST /hook/inject → stdout injection."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path

from cyt_client.agent import infer_harness_agent, looks_like_cursor_payload
from cyt_client.cursor import (
    format_cursor_continue,
    format_cursor_post_tool_stdout,
    format_cursor_stdout,
    is_cursor_hook_payload,
    is_session_end_event,
    is_session_start_event,
)
from cyt_client.pairing import repair_pairing
from cyt_client.port import (
    clear_hook_url_cache,
    find_hook_server_port_excluding,
    hook_url_for_port,
    resolve_hook_url,
)
from cyt_client.rules_file import (
    delete_cursor_rules_file,
    extract_additional_context,
    extract_cyt_agent,
    extract_phase_timing,
    extract_rules_merge_sections,
    extract_session_log_entries,
    format_phase_timing_verbose,
    hook_stdout_bytes_for_agent,
    is_valid_workspace_root,
    read_cursor_rules_injection,
    reset_cursor_rules_file_to_placeholder,
    set_rules_file_rel_path,
    sync_cursor_rules_file,
    workspace_root_from_payload,
)
from cyt_client.session_capture import (
    is_post_tool_capture_event,
    is_prompt_submit_event,
    persist_cyt_mcp_search_result,
    persist_turn_to_session_log,
)
from cyt_client.sessions import (
    append_session_log,
    cleanup_stale_session_logs,
    session_id_from_payload,
    session_log_path,
    sessions_dir_for_payload,
)
from cyt_client.skip import hook_skip_enabled
from cyt_client.tool_gate import (
    format_claude_deny,
    format_codex_deny,
    format_cursor_deny,
    is_pre_tool_event,
    validate_pre_tool_call,
)
from cyt_client.transcript import enrich_hook_payload
from cyt_client.transport import post_hook_inject

_verbose = False
_debug = False
_fresh_hook = False


def _parse_client_flags(argv: list[str] | None = None) -> tuple[bool, bool, bool, str | None]:
    parser = argparse.ArgumentParser(prog="cyt-client", add_help=False)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore existing .cursor/rules/cyt-injection.mdc for this hook call (CLI testing)",
    )
    parser.add_argument(
        "--rule",
        metavar="PATH",
        help="Cursor rules file path (relative to workspace); default: .cursor/rules/cyt-injection.mdc",
    )
    args, _unknown = parser.parse_known_args(argv)
    fresh = bool(args.fresh) or os.environ.get("CYT_CLI_FRESH_HOOK", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return bool(args.verbose), bool(args.debug), fresh, args.rule


def _verbose_log(message: str) -> None:
    if _verbose:
        print(message, file=sys.stderr, flush=True)


def _verbose_exception(context: str) -> None:
    if not _verbose:
        return
    print(f"cyt-client: {context}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)


def _parse_payload(raw: bytes) -> dict | None:
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_hook_stdout(body: bytes, *, cursor_output: bool) -> None:
    agent_body = hook_stdout_bytes_for_agent(body)
    if cursor_output:
        print(format_cursor_stdout(agent_body.decode()), flush=True)
        return
    if agent_body:
        sys.stdout.buffer.write(agent_body)
        sys.stdout.flush()


def _emit_cursor_continue() -> None:
    print(format_cursor_continue(), flush=True)


def _should_emit_cursor_continue_on_skip(
    payload: dict | None,
    *,
    cursor_output: bool,
) -> bool:
    if cursor_output:
        return True
    if os.environ.get("CYT_LAUNCH_AGENT", "").strip().lower() == "cursor":
        return True
    return payload is not None and looks_like_cursor_payload(payload)


def _emit_cursor_hook_stdout(body: bytes) -> None:
    print(format_cursor_stdout(hook_stdout_bytes_for_agent(body).decode()), flush=True)


def _workspace_for_cursor_hook(payload: dict) -> Path | None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        return None
    if not is_valid_workspace_root(workspace):
        _verbose_log(f"cyt-client: invalid workspace root: {workspace}")
        return None
    return workspace


def _post_hook_inject_resilient(
    hook_url: str,
    payload_bytes: bytes,
) -> tuple[int, bytes, str] | None:
    try:
        status, body = post_hook_inject(hook_url, payload_bytes, debug=_debug)
    except ConnectionError as exc:
        _verbose_log(f"cyt-client: hook server connection failed: {exc}")
        return None

    if status < 400:
        return status, body, hook_url

    excluded_port = None
    if match := re.search(r":(\d+)/", hook_url):
        try:
            excluded_port = int(match.group(1))
        except ValueError:
            excluded_port = None
    clear_hook_url_cache()
    fallback_port = find_hook_server_port_excluding(excluded_port)
    if fallback_port is not None:
        fallback_url = hook_url_for_port(fallback_port)
        if fallback_url != hook_url:
            _verbose_log(
                f"cyt-client: hook server returned HTTP {status}; retrying {fallback_url}",
            )
            try:
                status, body = post_hook_inject(fallback_url, payload_bytes, debug=_debug)
                return status, body, fallback_url
            except ConnectionError as exc:
                _verbose_log(f"cyt-client: hook server connection failed: {exc}")
                return None

    return status, body, hook_url


def _persist_session_log_response(payload: dict, body: bytes) -> None:
    entries = extract_session_log_entries(body)
    if not entries:
        return
    path = session_log_path(payload)
    if path is None:
        return
    agent = extract_cyt_agent(body)
    try:
        append_session_log(path, entries, agent=agent)
    except OSError as exc:
        _verbose_log(f"cyt-client: failed to append session log: {exc}")


def _reset_cursor_rules_file_for_session_lifecycle(payload: dict) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        return
    if is_valid_workspace_root(workspace):
        reset_cursor_rules_file_to_placeholder(workspace)
    else:
        _verbose_log(f"cyt-client: invalid workspace root: {workspace}")


def _handle_post_tool_capture(payload: dict, *, cursor_output: bool) -> None:
    try:
        persist_cyt_mcp_search_result(payload)
    except OSError as exc:
        _verbose_log(f"cyt-client: failed to persist search result: {exc}")
    if cursor_output:
        print(format_cursor_post_tool_stdout(), flush=True)


def _handle_pre_tool(payload: dict, *, cursor_output: bool) -> None:
    allowed, reason = validate_pre_tool_call(payload)
    if allowed:
        if cursor_output:
            print(format_cursor_continue(), flush=True)
        return
    agent = infer_harness_agent(payload) or os.environ.get("CYT_LAUNCH_AGENT", "").strip()
    if cursor_output or agent == "cursor":
        print(format_cursor_deny(reason), flush=True)
        raise SystemExit(2)
    if agent == "codex":
        print(format_codex_deny(reason), flush=True)
        raise SystemExit(2)
    print(format_claude_deny(reason), flush=True)
    raise SystemExit(2)


def _handle_session_start(payload: dict, *, cursor_output: bool) -> None:
    repair_pairing(payload, verbose=_verbose, session_start=True)
    if cursor_output:
        _reset_cursor_rules_file_for_session_lifecycle(payload)
        _emit_cursor_continue()


def _handle_session_end(payload: dict, *, cursor_output: bool) -> None:
    repair_pairing(payload, verbose=_verbose, session_start=False)
    if cursor_output:
        _reset_cursor_rules_file_for_session_lifecycle(payload)

    sessions_dir = sessions_dir_for_payload(payload)
    current_session_id = session_id_from_payload(payload)
    try:
        removed = cleanup_stale_session_logs(sessions_dir, current_session_id)
        if _verbose and removed:
            _verbose_log(f"cyt-client: removed {len(removed)} stale session log(s)")
    except OSError as exc:
        _verbose_log(f"cyt-client: failed to cleanup session logs: {exc}")

    if cursor_output:
        _emit_cursor_continue()


def _handle_cursor_before_submit(raw: bytes, payload: dict) -> None:  # noqa: C901
    workspace = _workspace_for_cursor_hook(payload)
    if workspace is None:
        _emit_cursor_continue()
        return

    prior_rules_injection = "" if _fresh_hook else read_cursor_rules_injection(workspace)
    payload_bytes = enrich_hook_payload(raw, rules_injection=prior_rules_injection)
    if is_prompt_submit_event(payload):
        try:
            enriched = json.loads(payload_bytes)
            if isinstance(enriched, dict):
                persist_turn_to_session_log(enriched)
        except (json.JSONDecodeError, OSError) as exc:
            _verbose_log(f"cyt-client: failed to persist turn: {exc}")
    hook_url = resolve_hook_url()
    if hook_url is None:
        _verbose_log("cyt-client: hook server unavailable")
        _emit_cursor_continue()
        return

    result = _post_hook_inject_resilient(hook_url, payload_bytes)
    if result is None:
        _emit_cursor_continue()
        return
    status, body, _hook_url = result

    if status >= 400:
        _verbose_log(f"cyt-client: hook server returned HTTP {status}")
        error_preview = body[:300].decode(errors="replace") if body else ""
        if error_preview:
            _verbose_log(f"cyt-client: hook error body: {error_preview}")
        _emit_cursor_continue()
        return

    _persist_session_log_response(payload, body)

    phase_timing = extract_phase_timing(body)
    if phase_timing:
        _verbose_log(format_phase_timing_verbose(phase_timing))

    injection = extract_additional_context(body)
    if not injection.strip():
        if prior_rules_injection.strip():
            _verbose_log(
                "cyt-client: hook returned no additionalContext; "
                "tools already present in rules file (pre-exposure skip)",
            )
        else:
            _verbose_log("cyt-client: hook returned no additionalContext; skipping rules file sync")
            delete_cursor_rules_file(workspace)
        _emit_cursor_hook_stdout(body)
        return

    try:
        sync_cursor_rules_file(
            workspace,
            injection,
            merge_sections=extract_rules_merge_sections(body),
        )
    except OSError as exc:
        _verbose_log(f"cyt-client: failed to sync rules file: {exc}")
        _emit_cursor_continue()
        return

    _emit_cursor_hook_stdout(body)


def _handle_non_cursor_hook(raw: bytes, payload: dict) -> None:
    if not raw.strip():
        return

    payload_bytes = enrich_hook_payload(raw)
    if is_prompt_submit_event(payload):
        try:
            enriched = json.loads(payload_bytes)
            if isinstance(enriched, dict):
                persist_turn_to_session_log(enriched)
        except (json.JSONDecodeError, OSError) as exc:
            _verbose_log(f"cyt-client: failed to persist turn: {exc}")
    hook_url = resolve_hook_url()
    if hook_url is None:
        _verbose_log("cyt-client: hook server unavailable")
        return

    try:
        status, body = post_hook_inject(hook_url, payload_bytes, debug=_debug)
    except ConnectionError as exc:
        _verbose_log(f"cyt-client: hook server connection failed: {exc}")
        return

    if status >= 400:
        _verbose_log(f"cyt-client: hook server returned HTTP {status}")
        return

    _persist_session_log_response(payload, body)
    _write_hook_stdout(body, cursor_output=False)


def _run_hook(raw: bytes, payload: dict | None, *, cursor_output: bool) -> None:
    if payload is None:
        if cursor_output:
            _emit_cursor_continue()
        return

    if is_post_tool_capture_event(payload):
        _handle_post_tool_capture(payload, cursor_output=cursor_output)
        return

    if is_pre_tool_event(payload):
        _handle_pre_tool(payload, cursor_output=cursor_output)
        return

    if is_session_end_event(payload):
        _handle_session_end(payload, cursor_output=cursor_output)
        return

    if is_session_start_event(payload):
        _handle_session_start(payload, cursor_output=cursor_output)
        return

    if cursor_output:
        _handle_cursor_before_submit(raw, payload)
        return

    _handle_non_cursor_hook(raw, payload)


def main(argv: list[str] | None = None) -> None:
    global _verbose, _debug, _fresh_hook
    _verbose, _debug, _fresh_hook, rule_path = _parse_client_flags(argv)

    cursor_output = False
    try:
        raw = sys.stdin.buffer.read()
        payload = _parse_payload(raw)
        cursor_output = payload is not None and is_cursor_hook_payload(payload)
        if hook_skip_enabled(payload):
            _verbose_log("cyt-client: skip.txt present; hook disabled")
            if _should_emit_cursor_continue_on_skip(payload, cursor_output=cursor_output):
                _emit_cursor_continue()
            return
        if cursor_output and rule_path:
            set_rules_file_rel_path(rule_path)
        _run_hook(raw, payload, cursor_output=cursor_output)
    except Exception:
        _verbose_exception("unexpected error")
        if cursor_output:
            _emit_cursor_continue()


if __name__ == "__main__":
    main()
