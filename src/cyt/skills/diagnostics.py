"""Shared dataclasses for skills search diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cyt.skills.search import MatchedSkill, SkillsPipelineRun


@dataclass(frozen=True)
class FrontmatterTokenContribution:
    stem: str
    score: float
    query_terms: tuple[str, ...]
    frontmatter_terms: tuple[str, ...]


@dataclass(frozen=True)
class FrontmatterGateRow:
    entry_dir: str
    doc_id: str
    file_path: str
    score: float | None
    passed: bool
    contributions: tuple[FrontmatterTokenContribution, ...] = ()
    raw_score: float | None = None


@dataclass(frozen=True)
class SearchItemRow:
    file_path: str
    doc_id: str
    item_id: str
    item_kind: str
    score: float
    passed: bool


@dataclass(frozen=True)
class BudgetItemRow:
    file_path: str
    item_id: str
    item_kind: str
    score: float
    tokens: int
    passed: bool


@dataclass(frozen=True)
class SkillsSearchTrace:
    frontmatter_limit: float
    frontmatter_rows: list[FrontmatterGateRow]
    pipeline_run: SkillsPipelineRun
    search_item_kind: str
    search_score_threshold: float | None
    search_rows: list[SearchItemRow]
    matches: list[MatchedSkill]
    injected: str | None = None
    inject_budget_max: int | None = None
    pre_budget_matches: tuple[MatchedSkill, ...] = ()
    budget_rows: list[BudgetItemRow] = field(default_factory=list)
