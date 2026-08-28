"""MCPC availability checks for hook CLI and runtime."""

from __future__ import annotations

import sys
from typing import Any, Literal, cast

from cyt.config import (
    load_config,
    tools_hook_mcpc_executable,
    uses_mcpc_tool_catalog,
)
from cyt.mcpc.cli import mcpc_available, run_mcpc_json

McpcSessionsProbe = Literal["ok", "empty", "unavailable"]

MCPC_INSTALL_HINT = "Please install mcpc: npm install -g @apify/mcpc"
MCPC_EMPTY_SESSIONS_HINT = (
    "Add at least one mcpc session, for example: mcpc connect https://mcp.context7.com/mcp @cn7"
)


def probe_mcpc_sessions(
    config: dict[str, Any] | None = None,
    *,
    quick: bool = False,
) -> McpcSessionsProbe | None:
    """Probe ``mcpc --json`` sessions when hook catalog source is mcpc."""
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg):
        return None

    executable = tools_hook_mcpc_executable(cfg)
    if not mcpc_available(executable):
        return "unavailable"
    if quick:
        return "ok"

    payload = run_mcpc_json(executable, [])
    if not isinstance(payload, dict):
        return "unavailable"

    payload_dict = cast(dict[str, Any], payload)
    sessions = payload_dict.get("sessions")
    if not isinstance(sessions, list):
        return "unavailable"

    if not sessions:
        return "empty"
    return "ok"


def report_mcpc_hook_readiness(
    config: dict[str, Any] | None = None,
    *,
    unattended: bool = False,
    quick: bool = False,
) -> None:
    """Print MCPC readiness hints to stderr when hook uses the mcpc catalog."""
    if unattended:
        return
    probe = probe_mcpc_sessions(config, quick=quick)
    if probe is None:
        return
    if probe == "unavailable":
        print(MCPC_INSTALL_HINT, file=sys.stderr)
        return
    if probe == "empty":
        print(MCPC_EMPTY_SESSIONS_HINT, file=sys.stderr)


def mcpc_hook_catalog_usable(
    config: dict[str, Any] | None = None,
    *,
    quick: bool = True,
) -> bool:
    """True when mcpc is configured and ``mcpc --json`` lists at least one session."""
    return probe_mcpc_sessions(config, quick=quick) == "ok"
