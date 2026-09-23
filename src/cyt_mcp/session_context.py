"""Per-request session runtime binding (contextvar only; no heavy imports)."""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyt_mcp.session_runtime import WorkspaceSessionRuntime

_current_session_runtime: contextvars.ContextVar[WorkspaceSessionRuntime | None] = (
    contextvars.ContextVar(
        "cyt_mcp_session_runtime",
        default=None,
    )
)


def get_current_session_runtime() -> WorkspaceSessionRuntime | None:
    return _current_session_runtime.get()


def set_current_session_runtime(
    runtime: WorkspaceSessionRuntime | None,
) -> contextvars.Token[WorkspaceSessionRuntime | None]:
    return _current_session_runtime.set(runtime)


def reset_current_session_runtime(token: contextvars.Token[WorkspaceSessionRuntime | None]) -> None:
    _current_session_runtime.reset(token)


_session_scoping_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "cyt_mcp_session_scoping_active",
    default=False,
)


def is_session_scoping_active() -> bool:
    return _session_scoping_active.get()


def set_session_scoping_active(active: bool) -> contextvars.Token[bool]:
    return _session_scoping_active.set(active)


def reset_session_scoping_active(token: contextvars.Token[bool]) -> None:
    _session_scoping_active.reset(token)
