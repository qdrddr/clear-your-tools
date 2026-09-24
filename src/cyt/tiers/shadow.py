"""Background shadow evaluation against dormant entities."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from cyt.tiers.adapters.tools import tool_entity_id
from cyt.tiers.config import TierMode, TierSectionConfig, tiers_active
from cyt.tiers.manager import NoOpTierManager, TierManager
from cyt.tiers.models import EntityTierState, Tier, ToolsTierApplyResult
from cyt.tiers.wake import evaluate_fast_wake

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cyt-tier-shadow")
_executor_lock = threading.Lock()


def _lexical_shadow_hits(
    query: str,
    dormant_ids: list[str],
    tools_by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, float]]:
    q = query.lower()
    hits: list[tuple[str, float]] = []
    for entity_id in dormant_ids:
        tool = tools_by_id.get(entity_id)
        if tool is None:
            continue
        name = str(tool.get("name") or "").lower()
        desc = str(tool.get("description") or "").lower()
        score = 0.0
        if name and name in q:
            score = 0.85
        elif name:
            parts = [part for part in name.replace("-", "_").split("_") if len(part) > 2]
            if any(part in q for part in parts):
                score = 0.55
        if desc:
            words = [word for word in desc.split() if len(word) > 4][:12]
            if any(word.lower() in q for word in words):
                score = max(score, 0.35)
        if score > 0.0:
            hits.append((entity_id, score))
    return hits


def schedule_tool_shadow_evaluation(
    *,
    config: dict[str, Any],
    query: str,
    original_tools: list[dict[str, Any]],
    tier_apply: ToolsTierApplyResult,
    manager: TierManager | NoOpTierManager,
) -> None:
    if not tiers_active(config, kind="tool") or not query.strip():
        return
    dormant_ids = list(tier_apply.excluded_t0)
    if not dormant_ids:
        return
    tools_by_id = {tool_entity_id(tool): tool for tool in original_tools if tool_entity_id(tool)}

    def _run() -> None:
        try:
            from cyt.tiers.config import tier_section_config

            section = tier_section_config(config, kind="tool")
            hits = _lexical_shadow_hits(query, dormant_ids, tools_by_id)
            manager.apply_shadow_tool_hits(hits, config, tools_by_id=tools_by_id)
            if section.mode == TierMode.SHADOW and hits:
                logger.debug("tool shadow hits: %d dormant tools", len(hits))
        except Exception:
            logger.exception("tool shadow evaluation failed")

    with _executor_lock:
        _executor.submit(_run)


def schedule_shadow_evaluation(
    *,
    config: dict[str, Any],
    scope_key: str,
    kind: str,
    query: str,
    dormant_entity_ids: list[str],
    evaluate_fn: Callable[[str, list[str]], list[tuple[str, float]]],
    apply_transition: Callable[[str, str, float], None],
) -> None:
    if not query.strip() or not dormant_entity_ids:
        return
    if not tiers_active(config, kind=kind):
        return

    def _run() -> None:
        try:
            hits = evaluate_fn(query, dormant_entity_ids[:50])
            for entity_id, score in hits:
                apply_transition(scope_key, entity_id, score)
        except Exception:
            logger.exception("shadow evaluation failed for %s", kind)

    with _executor_lock:
        _executor.submit(_run)


def _entity_mcp_server(entity_id: str, tools_by_id: dict[str, dict[str, Any]]) -> str | None:
    tool = tools_by_id.get(entity_id)
    if tool is not None:
        from cyt.tool_examples.identity import resolve_mcp_server_and_tool

        server, _bare = resolve_mcp_server_and_tool(tool)
        return server or None
    if ":" not in entity_id:
        return None
    wire_name = entity_id.split(":", 1)[1]
    from cyt.tool_examples.identity import resolve_mcp_server_and_tool_from_wire_name

    server, _bare = resolve_mcp_server_and_tool_from_wire_name(wire_name)
    return server or None


def _lexical_skill_shadow_hits(
    query: str,
    dormant_ids: list[str],
    skills_by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, float]]:
    q = query.lower()
    hits: list[tuple[str, float]] = []
    for entity_id in dormant_ids:
        skill = skills_by_id.get(entity_id)
        if skill is None:
            continue
        name = str(skill.get("name") or skill.get("doc_id") or "").lower()
        desc = str(skill.get("description") or "").lower()
        score = 0.0
        if name and name in q:
            score = 0.85
        elif name:
            parts = [part for part in name.replace("-", "_").split("_") if len(part) > 2]
            if any(part in q for part in parts):
                score = 0.55
        if desc:
            words = [word for word in desc.split() if len(word) > 4][:12]
            if any(word.lower() in q for word in words):
                score = max(score, 0.35)
        if score > 0.0:
            hits.append((entity_id, score))
    return hits


def schedule_skill_shadow_evaluation(
    *,
    config: dict[str, Any],
    query: str,
    dormant_entity_ids: list[str],
    skills_by_id: dict[str, dict[str, Any]],
    manager: TierManager | NoOpTierManager,
) -> None:
    if not tiers_active(config, kind="skill") or not query.strip():
        return
    if not dormant_entity_ids:
        return

    def _run() -> None:
        try:
            from cyt.tiers.config import tier_section_config

            section = tier_section_config(config, kind="skill")
            hits = _lexical_skill_shadow_hits(query, dormant_entity_ids, skills_by_id)
            manager.apply_shadow_skill_hits(hits, config)
            if section.mode == TierMode.SHADOW and hits:
                logger.debug("skill shadow hits: %d dormant skills", len(hits))
        except Exception:
            logger.exception("skill shadow evaluation failed")

    with _executor_lock:
        _executor.submit(_run)


def record_shadow_hits(
    states: dict[tuple[str, str], EntityTierState],
    *,
    kind: str,
    hits: list[tuple[str, float]],
    cfg: TierSectionConfig,
    wake_cycle_id: int,
    tools_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[Any]:
    from cyt.tiers.models import TierTransition

    transitions: list[TierTransition] = []
    woken: set[str] = set()
    hit_set = {entity_id for entity_id, score in hits if score > 0}

    def _wake_entity(entity_id: str, *, query_relevance: float, sibling_activity: float) -> None:
        if entity_id in woken:
            return
        key = (kind, entity_id)
        state = states.get(key)
        if state is None:
            state = EntityTierState(
                entity_id=entity_id,
                kind=kind,
                stable_tier=Tier.DORMANT,
                effective_tier=Tier.DORMANT,
            )
            states[key] = state
        if entity_id in hit_set:
            state.stats.shadow_hits += 1.0
        wake = evaluate_fast_wake(
            state,
            cfg=cfg,
            wake_cycle_id=wake_cycle_id,
            query_relevance=query_relevance,
            sibling_activity=sibling_activity,
        )
        if wake is not None:
            transitions.append(wake)
            woken.add(entity_id)

    for entity_id in hit_set:
        key = (kind, entity_id)
        state = states.get(key)
        if state is None:
            state = EntityTierState(
                entity_id=entity_id,
                kind=kind,
                stable_tier=Tier.DORMANT,
                effective_tier=Tier.DORMANT,
            )
            states[key] = state
        state.stats.shadow_evaluations += 1.0
        top_score = max((score for eid, score in hits if eid == entity_id), default=0.0)
        _wake_entity(entity_id, query_relevance=top_score, sibling_activity=0.0)

    server_max_score: dict[str, float] = {}
    if tools_by_id is not None:
        for entity_id in hit_set:
            server = _entity_mcp_server(entity_id, tools_by_id)
            if not server:
                continue
            top_score = max((score for eid, score in hits if eid == entity_id), default=0.0)
            server_max_score[server] = max(server_max_score.get(server, 0.0), top_score)

    if tools_by_id is not None and server_max_score:
        for state_key, state in states.items():
            if state_key[0] != kind or state.effective_tier != Tier.DORMANT:
                continue
            entity_id = state_key[1]
            server = _entity_mcp_server(entity_id, tools_by_id)
            if not server or server not in server_max_score:
                continue
            if entity_id not in hit_set:
                state.stats.shadow_evaluations += 1.0
            _wake_entity(
                entity_id,
                query_relevance=server_max_score[server],
                sibling_activity=0.0 if entity_id in hit_set else 1.0,
            )
    else:
        for state_key in states:
            if state_key[0] != kind:
                continue
            eid = state_key[1]
            if eid not in hit_set and states[state_key].effective_tier == Tier.DORMANT:
                states[state_key].stats.shadow_evaluations += 1.0

    return transitions
