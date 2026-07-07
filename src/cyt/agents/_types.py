"""Agent identifiers and env constants (no cyt.skills / cyt.launch imports)."""

from __future__ import annotations

from typing import Final, Literal

AgentName = Literal["claude", "codex", "cursor"]

LAUNCH_AGENTS: Final[tuple[AgentName, ...]] = ("claude", "codex", "cursor")

CYT_LAUNCH_AGENT_ENV = "CYT_LAUNCH_AGENT"
CYT_AGENT_FIELD = "cyt_agent"

__all__ = [
    "CYT_AGENT_FIELD",
    "CYT_LAUNCH_AGENT_ENV",
    "LAUNCH_AGENTS",
    "AgentName",
    "launch_agent_usage_hint",
    "unknown_launch_agent_message",
]


def launch_agent_usage_hint() -> str:
    lines = ["Acceptable agents:"]
    lines.extend(f"  {name}" for name in LAUNCH_AGENTS)
    lines.append("")
    lines.append("Usage:")
    lines.append("  cyt claude|codex|cursor [cyt options...]")
    lines.append("  cyt launch -- <agent> [agent args...]")
    return "\n".join(lines)


def unknown_launch_agent_message(raw: str) -> str:
    return f"Unknown agent {raw!r}.\n\n{launch_agent_usage_hint()}"
