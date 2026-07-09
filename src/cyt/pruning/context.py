"""Types for shared skills+tools pruning orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MAX_PRUNE_BATCH_WORKERS = 5

SkillsStage = Literal["bm25", "rerank", "llm"]
WorkUnitKind = Literal[
    "tools_pipeline",
    "tools_stage",
    "skills_search",
]


@dataclass
class PruneContext:
    query: str
    config: dict[str, Any]
    skill_entries: list[Any] = field(default_factory=list)
    skill_out: dict[str, Any] = field(default_factory=dict)
    pruner_settings: Any | None = None
    tools_effective: list[str] = field(default_factory=lambda: ["bm25"])
    skills_effective: SkillsStage = "bm25"
    skills_allowed: bool = False
    tools_allowed: bool = False
    upstream_kind: str | None = None


@dataclass(frozen=True)
class WorkUnit:
    kind: WorkUnitKind
    source_id: str = "root"
    stage: SkillsStage | None = None
    pipeline: tuple[str, ...] | None = None
