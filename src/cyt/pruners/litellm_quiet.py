"""Suppress LiteLLM console noise (hook stdout must stay pure JSON)."""

from __future__ import annotations

import os
from typing import Any, cast

_configured = False


def configure_litellm_quiet() -> None:
    """Idempotently quiet LiteLLM logging and debug banners."""
    global _configured
    if _configured:
        return
    _configured = True

    os.environ.setdefault("LITELLM_LOG", "ERROR")

    try:
        import litellm
    except ImportError:
        return

    quiet_litellm = cast(Any, litellm)
    quiet_litellm.set_verbose = False
    if hasattr(quiet_litellm, "suppress_debug_info"):
        quiet_litellm.suppress_debug_info = True
