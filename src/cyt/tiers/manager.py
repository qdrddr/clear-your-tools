"""Unified tier manager — hot-path snapshot and feedback API."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.indexer.policies import PolicyContext

from cyt.hook.workspace_config import hook_workspace_from_config
from cyt.tiers.adapters.skills import canonical_skill_entity_id, partition_skill_entries, skill_entity_id
from cyt.tiers.adapters.tools import apply_tool_tiers, merge_tool_policies, tool_entity_id
from cyt.tiers.config import (
    TierSectionConfig,
    resolve_tier_project,
    tier_section_config,
    tier_state_db_path,
    tiers_active,
    tiers_apply,
)
from cyt.tiers.evaluator import epoch_boundary, evaluate_slow_clock, expire_temporary_promotions
from cyt.tiers.models import (
    EntityKind,
    EntityTierState,
    EntityTierView,
    SkillsTierPartition,
    Tier,
    TierProject,
    TierSnapshot,
    TierTransition,
    ToolsTierApplyResult,
)
from cyt.tiers.store import TierStore
from cyt.tiers.wake import fast_promote_on_optional_use

logger = logging.getLogger(__name__)

_manager_lock = threading.RLock()
_managers: dict[str, TierManager] = {}


class NoOpTierManager:
    """Tier manager used when no project root is resolved."""

    project: TierProject | None = None
    is_noop = True

    def close(self) -> None:
        return

    def snapshot_tools(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="tool")
        return TierSnapshot(
            project=None,
            epoch_id=0,
            epoch_start_ms=0,
            session_id=0,
            entities={},
            shadow_mode=cfg.shadow,
            enabled=cfg.enabled,
        )

    def snapshot_skills(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="skill")
        return TierSnapshot(
            project=None,
            epoch_id=0,
            epoch_start_ms=0,
            session_id=0,
            entities={},
            shadow_mode=cfg.shadow,
            enabled=cfg.enabled,
        )

    def apply_tools(self, tools: list[dict[str, Any]], config: dict[str, Any]) -> ToolsTierApplyResult:
        return apply_tool_tiers(tools, tier_for_tool={}, apply=False)

    def merge_tool_policies_for_config(
        self,
        output_ctx: PolicyContext,
        result: ToolsTierApplyResult,
    ) -> PolicyContext:
        return merge_tool_policies(output_ctx, result.policy_overrides)

    def partition_skills(self, entries: list[Any], config: dict[str, Any]) -> SkillsTierPartition:
        return partition_skill_entries(entries, tier_for_skill={}, apply=False)

    def record_tool_candidates(self, tools: list[dict[str, Any]], config: dict[str, Any]) -> None:
        return

    def record_tools_injected(self, tools: list[dict[str, Any]], config: dict[str, Any]) -> None:
        return

    def record_tool_used(
        self,
        tool: dict[str, Any],
        *,
        config: dict[str, Any],
        optional_used: bool = False,
    ) -> None:
        return

    def record_skill_candidates(self, entries: list[Any], config: dict[str, Any]) -> None:
        return

    def record_skills_injected(self, matches: list[Any], config: dict[str, Any]) -> None:
        return

    def record_skill_used(self, entity_id: str, *, config: dict[str, Any]) -> None:
        return

    def apply_shadow_tool_hits(self, hits: list[tuple[str, float]], config: dict[str, Any]) -> None:
        return

    def status(self) -> dict[str, Any]:
        return {
            "project_id": None,
            "root_path": None,
            "histogram": {},
            "epoch_id": 0,
            "session_id": 0,
        }


_NOOP_MANAGER = NoOpTierManager()


class TierManager:
    is_noop = False

    def __init__(self, root_path: Path, db_path: str) -> None:
        self._store = TierStore.open(db_path)
        project_id = self._store.get_or_create_project(str(root_path))
        self.project = TierProject(project_id=project_id, root_path=root_path)
        self._states = self._store.load_entity_states(self.project)
        self._epoch = self._store.load_epoch_state(self.project)
        self._snapshot_tools: TierSnapshot | None = None
        self._snapshot_skills: TierSnapshot | None = None
        self._pending_flush = False
        self._last_injected_tools: set[str] = set()
        self._last_injected_skills: set[str] = set()
        self._rebuild_snapshots(enabled=False, shadow=True)

    def close(self) -> None:
        self._store.close()

    def _ensure_state(self, kind: str, entity_id: str) -> EntityTierState:
        key = (kind, entity_id)
        state = self._states.get(key)
        if state is None:
            state = EntityTierState(
                entity_id=entity_id,
                kind=kind,
                stable_tier=Tier.ACTIVE,
                effective_tier=Tier.ACTIVE,
            )
            self._states[key] = state
        return state

    def _effective_tier_for(self, state: EntityTierState) -> Tier:
        now_ms = int(time.time() * 1000)
        if state.temp_promotion_until_ms is not None and now_ms < state.temp_promotion_until_ms:
            return max(state.effective_tier, state.stable_tier)
        if state.overlap_tier is not None:
            return max(state.effective_tier, state.overlap_tier)
        return state.effective_tier

    def _build_snapshot(self, *, kind: str, cfg: TierSectionConfig) -> TierSnapshot:
        entities: dict[tuple[str, str], EntityTierView] = {}
        for key, state in self._states.items():
            if key[0] != kind:
                continue
            tier = self._effective_tier_for(state)
            temporary = (
                state.temp_promotion_until_ms is not None
                or state.overlap_tier is not None
                or state.effective_tier != state.stable_tier
            )
            entities[key] = EntityTierView(
                entity_id=state.entity_id,
                tier=tier,
                stable_tier=state.stable_tier,
                overlap_tier=state.overlap_tier,
                temporary=temporary,
            )
        return TierSnapshot(
            project=self.project,
            epoch_id=self._epoch.epoch_id,
            epoch_start_ms=self._epoch.epoch_start_ms,
            session_id=self._epoch.session_id,
            entities=entities,
            shadow_mode=cfg.shadow,
            enabled=cfg.enabled,
        )

    def _rebuild_snapshots(self, *, enabled: bool, shadow: bool) -> None:
        self._snapshot_tools = TierSnapshot(
            project=self.project,
            epoch_id=self._epoch.epoch_id,
            epoch_start_ms=self._epoch.epoch_start_ms,
            session_id=self._epoch.session_id,
            entities={
                key: EntityTierView(
                    entity_id=state.entity_id,
                    tier=self._effective_tier_for(state),
                    stable_tier=state.stable_tier,
                    overlap_tier=state.overlap_tier,
                    temporary=False,
                )
                for key, state in self._states.items()
                if key[0] == EntityKind.TOOL
            },
            shadow_mode=shadow,
            enabled=enabled,
        )
        self._snapshot_skills = TierSnapshot(
            project=self.project,
            epoch_id=self._epoch.epoch_id,
            epoch_start_ms=self._epoch.epoch_start_ms,
            session_id=self._epoch.session_id,
            entities={
                key: EntityTierView(
                    entity_id=state.entity_id,
                    tier=self._effective_tier_for(state),
                    stable_tier=state.stable_tier,
                    overlap_tier=state.overlap_tier,
                    temporary=False,
                )
                for key, state in self._states.items()
                if key[0] == EntityKind.SKILL
            },
            shadow_mode=shadow,
            enabled=enabled,
        )

    def snapshot_tools(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="tool")
        self._rebuild_snapshots(enabled=cfg.enabled, shadow=cfg.shadow)
        assert self._snapshot_tools is not None
        return self._snapshot_tools

    def snapshot_skills(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="skill")
        self._rebuild_snapshots(enabled=cfg.enabled, shadow=cfg.shadow)
        assert self._snapshot_skills is not None
        return self._snapshot_skills

    def _tier_map(self, snapshot: TierSnapshot) -> dict[str, Tier]:
        return {view.entity_id: view.tier for (_, _), view in snapshot.entities.items()}

    def apply_tools(
        self,
        tools: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> ToolsTierApplyResult:
        snapshot = self.snapshot_tools(config)
        tier_map = self._tier_map(snapshot)
        return apply_tool_tiers(tools, tier_for_tool=tier_map, apply=tiers_apply(config, kind="tool"))

    def merge_tool_policies_for_config(
        self,
        output_ctx: PolicyContext,
        result: ToolsTierApplyResult,
    ) -> PolicyContext:
        return merge_tool_policies(output_ctx, result.policy_overrides)

    def partition_skills(self, entries: list[Any], config: dict[str, Any]) -> SkillsTierPartition:
        snapshot = self.snapshot_skills(config)
        tier_map = self._tier_map(snapshot)
        return partition_skill_entries(
            entries,
            tier_for_skill=tier_map,
            apply=tiers_apply(config, kind="skill"),
        )

    def _decay_all(self, cfg: TierSectionConfig) -> None:
        for state in self._states.values():
            state.stats.decay(half_life=cfg.request_half_life)

    def _touch_request(self, config: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        tool_cfg = tier_section_config(config, kind="tool")
        self._decay_all(tool_cfg)
        self._epoch.last_request_ms = now_ms
        if epoch_boundary(now_ms=now_ms, epoch=self._epoch, cfg=tool_cfg):
            self._run_epoch(config)
        else:
            expire_temporary_promotions(self._states, now_ms=now_ms)
        self._store.save_epoch_state(self.project, self._epoch)

    def _run_epoch(self, config: dict[str, Any]) -> None:
        cfg = tier_section_config(config, kind="tool")
        now_ms = int(time.time() * 1000)
        transitions: list[TierTransition] = []
        transitions.extend(expire_temporary_promotions(self._states, now_ms=now_ms))
        transitions.extend(evaluate_slow_clock(self._states, cfg=cfg, epoch=self._epoch))
        for state in self._states.values():
            self._store.upsert_entity_state(self.project, state)
        if transitions:
            self._store.append_epoch_log(
                self.project,
                epoch_id=self._epoch.epoch_id,
                transitions=transitions,
            )
            if cfg.shadow:
                logger.info("tier shadow epoch %s transitions: %d", self._epoch.epoch_id, len(transitions))
        self._epoch.epoch_id += 1
        self._epoch.epoch_start_ms = now_ms

    def record_tool_candidates(self, tools: list[dict[str, Any]], config: dict[str, Any]) -> None:
        if not tiers_active(config, kind="tool"):
            return
        cfg = tier_section_config(config, kind="tool")
        for tool in tools:
            entity_id = tool_entity_id(tool)
            if not entity_id:
                continue
            state = self._ensure_state(EntityKind.TOOL, entity_id)
            state.stats.candidates += 1.0
            state.stats.last_seen_ms = int(time.time() * 1000)
        self._touch_request(config)
        self._flush_states(cfg)

    def record_tools_injected(
        self,
        tools: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> None:
        if not tiers_active(config, kind="tool"):
            return
        cfg = tier_section_config(config, kind="tool")
        injected_ids: set[str] = set()
        for tool in tools:
            entity_id = tool_entity_id(tool)
            if not entity_id:
                continue
            injected_ids.add(entity_id)
            state = self._ensure_state(EntityKind.TOOL, entity_id)
            state.stats.injected += 1.0
            state.stats.last_seen_ms = int(time.time() * 1000)
        self._last_injected_tools = injected_ids
        self._touch_request(config)
        self._flush_states(cfg)

    def record_tool_used(
        self,
        tool: dict[str, Any],
        *,
        config: dict[str, Any],
        optional_used: bool = False,
    ) -> None:
        if not tiers_active(config, kind="tool"):
            return
        cfg = tier_section_config(config, kind="tool")
        entity_id = tool_entity_id(tool)
        if not entity_id:
            return
        state = self._ensure_state(EntityKind.TOOL, entity_id)
        state.stats.used += 1.0
        if entity_id not in self._last_injected_tools:
            state.stats.used_without_injection += 1.0
        if optional_used:
            state.stats.optional_used += 1.0
            fast_promote_on_optional_use(state)
        self._touch_request(config)
        self._flush_states(cfg)

    def record_skill_candidates(self, entries: list[Any], config: dict[str, Any]) -> None:
        if not tiers_active(config, kind="skill"):
            return
        cfg = tier_section_config(config, kind="skill")
        for entry in entries:
            entity_id = skill_entity_id(entry)
            state = self._ensure_state(EntityKind.SKILL, entity_id)
            state.stats.candidates += 1.0
            state.stats.last_seen_ms = int(time.time() * 1000)
        self._touch_request(config)
        self._flush_states(cfg)

    def record_skills_injected(
        self,
        matches: list[Any],
        config: dict[str, Any],
    ) -> None:
        if not tiers_active(config, kind="skill"):
            return
        cfg = tier_section_config(config, kind="skill")
        injected: set[str] = set()
        for match in matches:
            path = getattr(match, "file_path", None) or getattr(match, "source_path", "")
            entity_id = canonical_skill_entity_id(str(path))
            if not entity_id:
                continue
            injected.add(entity_id)
            state = self._ensure_state(EntityKind.SKILL, entity_id)
            state.stats.injected += 1.0
            state.stats.last_seen_ms = int(time.time() * 1000)
        self._last_injected_skills = injected
        self._touch_request(config)
        self._flush_states(cfg)

    def record_skill_used(self, entity_id: str, *, config: dict[str, Any]) -> None:
        if not tiers_active(config, kind="skill"):
            return
        cfg = tier_section_config(config, kind="skill")
        canonical_id = canonical_skill_entity_id(entity_id)
        if not canonical_id:
            return
        state = self._ensure_state(EntityKind.SKILL, canonical_id)
        state.stats.used += 1.0
        if canonical_id not in self._last_injected_skills:
            state.stats.used_without_injection += 1.0
        self._touch_request(config)
        self._flush_states(cfg)

    def _flush_states(self, cfg: TierSectionConfig) -> None:
        del cfg
        for state in self._states.values():
            self._store.upsert_entity_state(self.project, state)

    def apply_shadow_tool_hits(self, hits: list[tuple[str, float]], config: dict[str, Any]) -> None:
        from cyt.tiers.shadow import record_shadow_hits

        cfg = tier_section_config(config, kind="tool")
        transitions = record_shadow_hits(
            self._states,
            kind=EntityKind.TOOL,
            hits=hits,
            cfg=cfg,
            session_id=self._epoch.session_id,
        )
        for state in self._states.values():
            self._store.upsert_entity_state(self.project, state)
        if transitions and cfg.shadow:
            logger.debug("tool shadow transitions: %d", len(transitions))

    def status(self) -> dict[str, Any]:
        summary = self._store.status_summary(self.project)
        summary["epoch_id"] = self._epoch.epoch_id
        summary["session_id"] = self._epoch.session_id
        return summary


def _resolve_manager_workspace(
    config: dict[str, Any],
    *,
    workspace: Path | None,
) -> Path | None:
    if workspace is not None:
        return resolve_tier_project(workspace=workspace)
    hook_workspace = hook_workspace_from_config(config)
    if hook_workspace is not None:
        return resolve_tier_project(workspace=hook_workspace)
    return resolve_tier_project()


def get_tier_manager(
    config: dict[str, Any],
    *,
    workspace: Path | None = None,
) -> TierManager | NoOpTierManager:
    root_path = _resolve_manager_workspace(config, workspace=workspace)
    if root_path is None:
        return _NOOP_MANAGER
    key = str(root_path)
    with _manager_lock:
        manager = _managers.get(key)
        if manager is None:
            manager = TierManager(root_path, tier_state_db_path(config))
            _managers[key] = manager
        return manager
