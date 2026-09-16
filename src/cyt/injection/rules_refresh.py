"""Rules-file refresh signals for hook pre-exposure bypass."""

from __future__ import annotations

from typing import Any

from cyt.injection.pre_exposure_context import PreExposureContext

_INJECTION_CONTEXT_KINDS = frozenset({"tool", "skill", "resource"})


def session_has_injection_context(ctx: PreExposureContext) -> bool:
    """True when the session log or transcript already carries injected tool/skill context."""
    if any(entry.get("kind") in _INJECTION_CONTEXT_KINDS for entry in ctx.index.entries):
        return True
    combined = ctx.combined_text.strip()
    if not combined:
        return False
    lowered = combined.casefold()
    return "<agent-tools" in lowered or "<agent-skills" in lowered


def _force_refresh_flag_from_payload(payload: dict[str, Any]) -> bool:
    raw = payload.get("cyt_force_rules_refresh")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().casefold() in {"1", "true", "yes", "on"}
    return False


def bypass_injection_pre_exposure(
    payload: dict[str, Any],
    ctx: PreExposureContext | None = None,
) -> bool:
    """True when the Cursor rules file has no substantive injection and must be repopulated.

    Lifecycle placeholders written after a pre-exposure skip must not bypass session gating:
    the session log and transcript still carry the injected tool definitions.
    """
    if not _force_refresh_flag_from_payload(payload):
        return False
    if ctx is not None and session_has_injection_context(ctx):
        return False
    return True
