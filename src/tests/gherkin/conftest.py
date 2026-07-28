"""Shared fixtures for Gherkin scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from cyt.common.agents import AgentName
from tests.test_llm_prune_integration import (
    DEFAULT_USER_PROMPT,
    HookDaemonTrace,
    LlmPruneTrace,
    ScenarioMode,
)


@dataclass
class GherkinContext:
    """Mutable state shared between Gherkin steps in one scenario."""

    mode: ScenarioMode | None = None
    agent: AgentName = "cursor"
    prompt: str = DEFAULT_USER_PROMPT
    config: dict[str, Any] = field(default_factory=dict)
    selector_trace: LlmPruneTrace | None = None
    daemon_trace: HookDaemonTrace | None = None
    rule_path: str | None = None
    tmp_path: Path | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""


@pytest.fixture
def gherkin_context() -> GherkinContext:
    return GherkinContext()
