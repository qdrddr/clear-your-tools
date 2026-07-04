"""LLM selection over decomposed skill nodes (not BM25 chunks)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cyt.common.paths import shorten_home_path
from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.pruners.llm import (
    SELECTOR_NO_MATCH_INSTRUCTION,
    SELECTOR_SYSTEM_PROMPT,
    apply_selector_ids_to_catalog,
    llm_catalog_dict,
    llm_select_ids,
    prepare_catalog_selector_chunks,
    trim_catalog_dict,
)
from cyt.pruners.remote import LlmPruningSettings
from cyt.skills.catalog import (
    SkillEntryRef,
    _iter_content_node_ids,
)
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.nodes import load_node_body, skill_name
from cyt.skills.reconstruct import reconstruct_matches_from_survivor_dicts
from cyt.skills.search import MatchedSkill

logger = logging.getLogger(__name__)

SKILLS_SELECTOR_SYSTEM_PROMPT = (
    'These are agent skills in a "decomposed" state, represented as skill nodes. '
    "Each skill-node has a global selector id attribute. "
    "Your task is to select the most relevant skill-node(s) based on the user query. "
    "Later the selected nodes will be recompiled into partial skill markdown for another LLM. "
    "Return the selector id values from the skill-node id attributes that match the user query. "
    "Choose nodes that could potentially help fulfill the request while omitting irrelevant noise. "
    f"{SELECTOR_NO_MATCH_INSTRUCTION}"
)

COMBINED_SELECTOR_SYSTEM_PROMPT = (
    f"{SELECTOR_SYSTEM_PROMPT}\n\n"
    f"{SKILLS_SELECTOR_SYSTEM_PROMPT}\n\n"
    "The available items include MCP tool chunks (<chunk id=N>) and agent skill nodes "
    "(<skill-node id=N>). Return selector ids from both kinds that match the user query."
)


@dataclass(frozen=True)
class SkillNodeMeta:
    entry_dir: str
    doc_id: str
    node_id: int
    file_path: str


def prepare_skill_nodes(
    entries: list[SkillEntryRef],
    *,
    start_id: int = 1,
) -> tuple[list[str], dict[int, SkillNodeMeta]]:
    """Format skill nodes for the LLM selector; return one XML block per skill."""
    sorted_entries = sorted(entries, key=lambda entry: shorten_home_path(entry.source_path))
    formatted_items: list[str] = []
    metadata: dict[int, SkillNodeMeta] = {}
    selector_id = start_id

    for entry in sorted_entries:
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = shorten_home_path(entry.source_path)
        name = skill_name(entry)
        node_lines: list[str] = []
        for node_id in _iter_content_node_ids(structure):
            body = load_node_body(entry, node_id)
            if not body:
                continue
            metadata[selector_id] = SkillNodeMeta(
                entry_dir=entry.entry_dir,
                doc_id=entry.doc_id,
                node_id=node_id,
                file_path=file_path,
            )
            node_lines.append(f'<skill-node id="{selector_id}">\n{body}\n</skill-node>')
            selector_id += 1

        if not node_lines:
            continue

        name_attr = f' name="{name}"' if name else ""
        skill_block = "\n".join(
            [
                "<agent-skills>",
                f'<skill Path="{file_path}"{name_attr}>',
                *node_lines,
                "</skill>",
                "</agent-skills>",
            ],
        )
        formatted_items.append(skill_block)

    return formatted_items, metadata


def reconstruct_skills_from_llm_ids(
    metadata: dict[int, SkillNodeMeta],
    selected_ids: set[int],
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> list[MatchedSkill]:
    """Map surviving selector ids to node_id_specs and rebuild MatchedSkill list."""
    del config
    survivors: list[dict[str, Any]] = []
    for selector_id in selected_ids:
        meta = metadata.get(selector_id)
        if meta is None:
            continue
        survivors.append(
            {
                "entry_dir": meta.entry_dir,
                "doc_id": meta.doc_id,
                "node_id": meta.node_id,
                "file_path": meta.file_path,
                "score": 1.0,
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

    formatted_items, metadata = prepare_skill_nodes(entries)
    if not formatted_items:
        return [], [], empty_usage()

    selected_ids, usage = llm_select_ids(
        query,
        SKILLS_SELECTOR_SYSTEM_PROMPT,
        formatted_items,
        config=config,
        settings=settings,
    )
    search_rows: list[SearchItemRow] = []
    for selector_id, meta in metadata.items():
        selected = selector_id in selected_ids
        search_rows.append(
            SearchItemRow(
                file_path=meta.file_path,
                doc_id=meta.doc_id,
                item_id=str(meta.node_id),
                item_kind="node",
                score=1.0 if selected else 0.0,
                passed=selected,
            ),
        )
    matches = reconstruct_skills_from_llm_ids(metadata, selected_ids, entries, config=config)
    return matches, search_rows, usage


def llm_prune_tools_and_skills(
    data: dict[str, Any],
    query: str,
    skill_entries: list[SkillEntryRef],
    *,
    trim_before_llm: bool = False,
    config: dict[str, Any] | None = None,
    settings: LlmPruningSettings | None = None,
) -> tuple[dict[str, Any], list[MatchedSkill], StageTokenUsage]:
    """Combined tool catalog + skill node LLM selection in one bulk when possible."""
    if trim_before_llm:
        data = trim_catalog_dict(data)

    tool_chunks, tool_metadata, list_keys = prepare_catalog_selector_chunks(data)
    last_tool_id = max(tool_metadata.keys(), default=0)
    skill_items, skill_metadata = prepare_skill_nodes(skill_entries, start_id=last_tool_id + 1)

    combined_items = tool_chunks + skill_items
    if not combined_items:
        return data, [], empty_usage()

    try:
        selected_ids, usage = llm_select_ids(
            query,
            COMBINED_SELECTOR_SYSTEM_PROMPT,
            combined_items,
            config=config,
            settings=settings,
        )
    except Exception as exc:
        logger.warning("combined llm prune failed, falling back to sequential: %s", exc)
        pruned_data, tool_usage = llm_catalog_dict(
            data,
            query,
            merge_pinned=False,
            config=config,
            settings=settings,
        )
        skill_matches, skill_usage = llm_skill_nodes(
            query,
            skill_entries,
            config=config,
            settings=settings,
        )
        return pruned_data, skill_matches, tool_usage.merge(skill_usage)

    tool_selected = {sid for sid in selected_ids if sid in tool_metadata}
    skill_selected = {sid for sid in selected_ids if sid in skill_metadata}
    result = apply_selector_ids_to_catalog(data, tool_metadata, tool_selected, list_keys)
    skill_matches = reconstruct_skills_from_llm_ids(
        skill_metadata,
        skill_selected,
        skill_entries,
        config=config,
    )
    return result, skill_matches, usage
