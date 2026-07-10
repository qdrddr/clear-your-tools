"""LLM selection over decomposed skill nodes (not BM25 chunks)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt.common.paths import shorten_home_path
from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.pruners.llm import (
    SELECTOR_NO_MATCH_INSTRUCTION,
    SELECTOR_SCORE_INSTRUCTION,
    llm_select_ids,
)
from cyt.pruners.remote import LlmPruningSettings
from cyt.pruners.selector_xml import (
    SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
    SkillSelectorBlockRow,
    format_selector_soft_budget_line,
    selector_id_attr,
    selector_tokens_attr,
    selector_total_tokens_attr,
)
from cyt.skills.catalog import (
    SkillEntryRef,
    _iter_content_node_ids,
)
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.nodes import load_node_content, skill_name
from cyt.skills.reconstruct import reconstruct_matches_from_survivor_dicts
from cyt.skills.search import MatchedSkill

_SKILLS_SELECTOR_SYSTEM_PROMPT_PREFIX = (
    'These are agent skills in a "decomposed" state, represented as skill nodes. '
    "Each skill-node has a global selector id attribute. "
    "Your task is to select the most relevant skill-node(s) based on the user query. "
    "Later the selected nodes will be recompiled into partial skill markdown for another LLM. "
    f"{SELECTOR_SCORE_INSTRUCTION}"
    "Return selector id values from the skill-node id attributes that match the user query. "
    "Choose nodes that could potentially help fulfill the request while omitting irrelevant noise. "
    "Each skill-node and skill tag includes a tokens attribute; agent-skills includes total-tokens. "
)


def build_skills_selector_system_prompt(*, soft_budget: int) -> str:
    return (
        f"{_SKILLS_SELECTOR_SYSTEM_PROMPT_PREFIX}"
        f"{format_selector_soft_budget_line(soft_budget, target='nodes')} "
        f"{SELECTOR_NO_MATCH_INSTRUCTION}"
    )


def skills_selector_system_prompt(
    *,
    soft_budget: int = SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
) -> str:
    return build_skills_selector_system_prompt(soft_budget=soft_budget)


SKILLS_SELECTOR_SYSTEM_PROMPT = skills_selector_system_prompt()


@dataclass(frozen=True)
class SkillNodeMeta:
    entry_dir: str
    doc_id: str
    node_id: int
    file_path: str
    token_count: int | None = None


def prepare_skill_nodes(
    entries: list[SkillEntryRef],
    *,
    start_id: int = 1,
) -> tuple[list[str], dict[int, SkillNodeMeta], list[int], list[SkillSelectorBlockRow]]:
    """Format skill nodes for the LLM selector; return one XML block per skill."""
    sorted_entries = sorted(entries, key=lambda entry: shorten_home_path(entry.source_path))
    formatted_items: list[str] = []
    item_token_counts: list[int] = []
    block_rows: list[SkillSelectorBlockRow] = []
    metadata: dict[int, SkillNodeMeta] = {}
    selector_id = start_id

    for entry in sorted_entries:
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = shorten_home_path(entry.source_path)
        name = skill_name(entry)
        node_lines: list[str] = []
        block_node_ids: list[int] = []
        skill_token_total = 0
        for node_id in _iter_content_node_ids(structure):
            body, token_count = load_node_content(entry, node_id)
            if not body:
                continue
            metadata[selector_id] = SkillNodeMeta(
                entry_dir=entry.entry_dir,
                doc_id=entry.doc_id,
                node_id=node_id,
                file_path=file_path,
                token_count=token_count,
            )
            block_node_ids.append(selector_id)
            node_tokens_attr = selector_tokens_attr(token_count)
            if token_count:
                skill_token_total += token_count
            node_lines.append(
                f"<skill-node{selector_id_attr(selector_id)}{node_tokens_attr}>\n{body}\n</skill-node>",
            )
            selector_id += 1

        if not node_lines:
            continue

        name_attr = f' name="{name}"' if name else ""
        skill_tokens_attr = selector_tokens_attr(skill_token_total or None)
        skill_block = "\n".join(
            [
                f"<agent-skills{selector_total_tokens_attr(skill_token_total)}>",
                f'<skill Path="{file_path}"{name_attr}{skill_tokens_attr}>',
                *node_lines,
                "</skill>",
                "</agent-skills>",
            ],
        )
        formatted_items.append(skill_block)
        item_token_counts.append(skill_token_total)
        block_rows.append(
            SkillSelectorBlockRow(
                file_path=file_path,
                name=name,
                total_tokens=skill_token_total,
                node_selector_ids=tuple(block_node_ids),
            ),
        )

    return formatted_items, metadata, item_token_counts, block_rows


def reconstruct_skills_from_llm_ids(
    metadata: dict[int, SkillNodeMeta],
    selected_scores: dict[int, int],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Map surviving selector ids to node_id_specs and rebuild MatchedSkill list."""
    del config
    survivors: list[dict[str, Any]] = []
    for selector_id, llm_score in selected_scores.items():
        meta = metadata.get(selector_id)
        if meta is None:
            continue
        survivors.append(
            {
                "entry_dir": meta.entry_dir,
                "doc_id": meta.doc_id,
                "node_id": meta.node_id,
                "file_path": meta.file_path,
                "score": llm_score / 100.0,
            },
        )
    return reconstruct_matches_from_survivor_dicts(
        survivors,
        entries,
        item_kind="node",
        id_field="node_id",
    )


def llm_skill_nodes(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    matches, _rows, usage = llm_skill_nodes_with_trace(
        query,
        entries,
        config=config,
        settings=settings,
    )
    return matches, usage


def llm_skill_nodes_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
) -> tuple[list[MatchedSkill], list[SearchItemRow], StageTokenUsage]:
    """Select skill nodes via LLM and return selected node ids."""
    if not query.strip() or not entries:
        return [], [], empty_usage()

    formatted_items, metadata, item_token_counts, _block_rows = prepare_skill_nodes(entries)
    if not formatted_items:
        return [], [], empty_usage()

    selected_scores, usage = llm_select_ids(
        query,
        SKILLS_SELECTOR_SYSTEM_PROMPT,
        formatted_items,
        chunk_token_counts=item_token_counts,
        system_prompt_for_budget=lambda budget: skills_selector_system_prompt(soft_budget=budget),
        soft_budget_total=SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
        config=config,
        settings=settings,
    )
    search_rows: list[SearchItemRow] = []
    for selector_id, meta in metadata.items():
        llm_score = selected_scores.get(selector_id, 0)
        passed = selector_id in selected_scores
        search_rows.append(
            SearchItemRow(
                file_path=meta.file_path,
                doc_id=meta.doc_id,
                item_id=str(meta.node_id),
                item_kind="node",
                score=llm_score / 100.0,
                passed=passed,
            ),
        )
    matches = reconstruct_skills_from_llm_ids(
        metadata,
        selected_scores,
        entries,
        config=config,
    )
    return matches, search_rows, usage
