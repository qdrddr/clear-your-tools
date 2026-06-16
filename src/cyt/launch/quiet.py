"""Silence CYT console output during ``cyt launch``."""

from __future__ import annotations

import logging

from cyt.skills.hook_quiet import configure_hook_quiet

_configured = False


def configure_launch_quiet() -> None:
    """Idempotently silence CYT and library logging during agent launch."""
    global _configured
    if _configured:
        return
    _configured = True
    configure_hook_quiet()
    logging.disable(logging.CRITICAL)
