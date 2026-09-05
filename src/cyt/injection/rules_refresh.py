"""Rules-file refresh signals for hook pre-exposure bypass."""

from __future__ import annotations

from typing import Any


def bypass_injection_pre_exposure(payload: dict[str, Any]) -> bool:
    """True when the Cursor rules file has no substantive injection and must be repopulated."""
    raw = payload.get("cyt_force_rules_refresh")
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().casefold() in {"1", "true", "yes", "on"}
    return False
