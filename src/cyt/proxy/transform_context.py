"""Threaded agent/session context for proxy request transforms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt.injection.pre_exposure_context import PreExposureContext


@dataclass(frozen=True)
class ProxyTransformContext:
    agent: str
    session_id: str | None
    pre_exposure_ctx: PreExposureContext | None = None

    @classmethod
    def from_request(
        cls,
        *,
        agent: str | None,
        session_id: str | None,
        body: dict[str, Any],
        kind: str,
    ) -> ProxyTransformContext | None:
        if not agent or not agent.strip():
            return None
        resolved_agent = agent.strip()
        resolved_session = (
            session_id.strip() if isinstance(session_id, str) and session_id else None
        )
        pre_exposure_ctx = None
        if resolved_session:
            pre_exposure_ctx = PreExposureContext.for_proxy(
                body,
                "anthropic" if kind == "anthropic" else "openai",
                agent=resolved_agent,
                session_id=resolved_session,
            )
        return cls(
            agent=resolved_agent,
            session_id=resolved_session,
            pre_exposure_ctx=pre_exposure_ctx,
        )
