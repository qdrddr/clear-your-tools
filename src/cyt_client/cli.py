"""``cyt-client`` entry point: stdin JSON → POST /hook/inject → stdout injection."""

from __future__ import annotations

import argparse
import json
import sys
import traceback

from cyt_client.cursor import (
    format_cursor_continue,
    format_cursor_stdout,
    is_cursor_hook_payload,
    is_session_end_event,
)
from cyt_client.port import resolve_hook_url
from cyt_client.rules_file import (
    delete_cursor_rules_file,
    extract_additional_context,
    extract_cyt_agent,
    extract_rules_merge_sections,
    extract_session_log_entries,
    hook_stdout_bytes_for_agent,
    is_valid_workspace_root,
    read_cursor_rules_injection,
    set_rules_file_rel_path,
    sync_cursor_rules_file,
    workspace_root_from_payload,
)
from cyt_client.sessions import (
    append_session_log,
    cleanup_stale_session_logs,
    session_id_from_payload,
    session_log_path,
    sessions_dir_for_payload,
)
from cyt_client.transcript import enrich_hook_payload
from cyt_client.transport import post_hook_inject

_verbose = False
_debug = False


def _parse_client_flags(argv: list[str] | None = None) -> tuple[bool, bool, str | None]:
    parser = argparse.ArgumentParser(prog="cyt-client", add_help=False)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--rule",
        metavar="PATH",
        help="Cursor rules file path (relative to workspace); default: .cursor/rules/cyt-injection.mdc",
    )
    args, _unknown = parser.parse_known_args(argv)
    return bool(args.verbose), bool(args.debug), args.rule


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


def _handle_session_end(payload: dict, *, cursor_output: bool) -> None:
    if cursor_output:
        workspace = workspace_root_from_payload(payload)
        if workspace is not None:
            if is_valid_workspace_root(workspace):
                delete_cursor_rules_file(workspace)
            else:
                _verbose_log(f"cyt-client: invalid workspace root: {workspace}")

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


def _handle_cursor_before_submit(raw: bytes, payload: dict) -> None:
    workspace = workspace_root_from_payload(payload)
    if workspace is None:
        _emit_cursor_continue()
        return

    if not is_valid_workspace_root(workspace):
        _verbose_log(f"cyt-client: invalid workspace root: {workspace}")
        _emit_cursor_continue()
        return

    prior_rules_injection = read_cursor_rules_injection(workspace)
    payload_bytes = enrich_hook_payload(raw, rules_injection=prior_rules_injection)
    hook_url = resolve_hook_url()
    if hook_url is None:
        _verbose_log("cyt-client: hook server unavailable")
        _emit_cursor_continue()
        return

    try:
        status, body = post_hook_inject(hook_url, payload_bytes, debug=_debug)
    except ConnectionError as exc:
        _verbose_log(f"cyt-client: hook server connection failed: {exc}")
        _emit_cursor_continue()
        return

    if status >= 400:
        _verbose_log(f"cyt-client: hook server returned HTTP {status}")
        _emit_cursor_continue()
        return

    _persist_session_log_response(payload, body)

    injection = extract_additional_context(body)
    if not injection.strip():
        _verbose_log("cyt-client: hook returned no additionalContext; skipping rules file sync")
        delete_cursor_rules_file(workspace)
        print(format_cursor_stdout(hook_stdout_bytes_for_agent(body).decode()), flush=True)
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

    print(format_cursor_stdout(hook_stdout_bytes_for_agent(body).decode()), flush=True)


def _run_hook(raw: bytes, payload: dict | None, *, cursor_output: bool) -> None:
    if payload is None:
        if cursor_output:
            _emit_cursor_continue()
        return

    if is_session_end_event(payload):
        _handle_session_end(payload, cursor_output=cursor_output)
        return

    if cursor_output:
        _handle_cursor_before_submit(raw, payload)
        return

    if not raw.strip():
        return

    payload_bytes = enrich_hook_payload(raw)
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


def main(argv: list[str] | None = None) -> None:
    global _verbose, _debug
    _verbose, _debug, rule_path = _parse_client_flags(argv)

    cursor_output = False
    try:
        raw = sys.stdin.buffer.read()
        payload = _parse_payload(raw)
        cursor_output = payload is not None and is_cursor_hook_payload(payload)
        if cursor_output and rule_path:
            set_rules_file_rel_path(rule_path)
        _run_hook(raw, payload, cursor_output=cursor_output)
    except Exception:
        _verbose_exception("unexpected error")
        if cursor_output:
            _emit_cursor_continue()


if __name__ == "__main__":
    main()
