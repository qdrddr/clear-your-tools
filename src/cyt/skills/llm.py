"""LLM selection over decomposed skill nodes (not BM25 chunks)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from cyt_indexer import load_skills_index_from_dir, reconstruct_skill_markdown

from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.indexer.tokens import count_tokens
from cyt.pruners.llm import (
    SELECTOR_SYSTEM_PROMPT,
    apply_selector_ids_to_catalog,
    llm_catalog_dict,
    llm_select_ids,
    prepare_catalog_selector_chunks,
    trim_catalog_dict,
)
from cyt.skills.catalog import SkillEntryRef, _iter_content_node_ids, _shorten_home_path
from cyt.skills.diagnostics import SearchItemRow
from cyt.skills.frontmatter import skill_name_from_frontmatter
from cyt.skills.nodes import load_node_body, skill_name
from cyt.skills.search import (
    MatchedSkill,
    _frontmatter_by_doc,
)

logger = logging.getLogger(__name__)

SKILLS_SELECTOR_SYSTEM_PROMPT = (
    'These are agent skills in a "decomposed" state, represented as skill nodes. '
    "Each skill-node has a global selector id attribute. "
    "Your task is to select the most relevant skill-node(s) based on the user query. "
    "Later the selected nodes will be recompiled into partial skill markdown for another LLM. "
    "Return the selector id values from the skill-node id attributes that match the user query. "
    "Choose nodes that could potentially help fulfill the request while omitting irrelevant noise."
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
    sorted_entries = sorted(entries, key=lambda entry: _shorten_home_path(entry.source_path))
    formatted_items: list[str] = []
    metadata: dict[int, SkillNodeMeta] = {}
    selector_id = start_id

    for entry in sorted_entries:
        structure = entry.document.get("structure")
        if not structure:
            continue
        file_path = _shorten_home_path(entry.source_path)
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
    by_doc: dict[tuple[str, str], list[int]] = {}
    file_path_by_doc: dict[tuple[str, str], str] = {}
    for selector_id in selected_ids:
        meta = metadata.get(selector_id)
        if meta is None:
            continue
        key = (meta.entry_dir, meta.doc_id)
        by_doc.setdefault(key, []).append(meta.node_id)
        file_path_by_doc[key] = meta.file_path

    if not by_doc:
        return []

    frontmatter_by_doc = _frontmatter_by_doc(entries)
    matched: list[MatchedSkill] = []
    for (entry_dir, doc_id), node_ids in by_doc.items():
        index = load_skills_index_from_dir(entry_dir)
        node_specs = [str(node_id) for node_id in sorted(set(node_ids))]
        result = reconstruct_skill_markdown(
            index,
            doc_id,
            node_id_specs=node_specs,
        )
        markdown = str(result.get("markdown", "")).strip()
        if not markdown:
            continue
        file_path = file_path_by_doc.get((entry_dir, doc_id), "")
        frontmatter = frontmatter_by_doc.get((entry_dir, doc_id))
        name = skill_name_from_frontmatter(frontmatter)
        token_count = count_tokens(markdown)
        matched.append(
            MatchedSkill(
                doc_id=doc_id,
                file_path=file_path,
                markdown=markdown,
                name=name,
                score=1.0,
                token_count=token_count,
            ),
        )

    return matched


def llm_skill_nodes(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
) -> tuple[list[MatchedSkill], StageTokenUsage]:
    matches, _rows, usage = llm_skill_nodes_with_trace(query, entries, config=config)
    return matches, usage


def llm_skill_nodes_with_trace(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any] | None = None,
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
        )
    except Exception as exc:
        logger.warning("combined llm prune failed, falling back to sequential: %s", exc)
        pruned_data, tool_usage = llm_catalog_dict(data, query, merge_pinned=False)
        skill_matches, skill_usage = llm_skill_nodes(query, skill_entries, config=config)
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
