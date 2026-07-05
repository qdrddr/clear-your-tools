"""Agent identifiers shared across launch, proxy, and skills."""

from __future__ import annotations

from typing import Literal

AgentName = Literal["claude", "codex", "cursor"]

__all__ = ["AgentName"]
