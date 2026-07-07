"""Lazy agent registry — import agent subpackages only inside get_agent()."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, cast

from cyt.agents._types import AgentName

if TYPE_CHECKING:
    from cyt.agents._protocol import AgentCapabilities


def get_agent(name: AgentName) -> AgentCapabilities:
    module = importlib.import_module(f"cyt.agents.{name}")
    capabilities = getattr(module, "capabilities", None)
    if capabilities is None:
        raise RuntimeError(f"agent module cyt.agents.{name} missing capabilities()")
    return cast("AgentCapabilities", capabilities())
