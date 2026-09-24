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
from cyt.tiers.adapters.skills import (
    normalize_skill_entity_states,
    partition_skill_entries,
    resolve_skill_doc_id,
    skill_entity_id,
    tier_entity_id_for_skill,
)
from cyt.tiers.adapters.tools import (
    apply_tool_tiers,
    canonical_tool_entity_id,
    merge_tool_policies,
    normalize_tool_entity_states,
    tool_entity_id,
)
from cyt.tiers.config import (
    TierMode,
    TierSectionConfig,
    resolve_tier_project,
    tier_disk_flush_seconds,
    tier_section_config,
    tier_state_db_path,
    tiers_active,
    tiers_apply,
)
from cyt.tiers.evaluator import (
    crystallize_successful_tool_promotions_at_epoch,
    epoch_age_expired,
    epoch_boundary,
    epoch_remaining_ms,
    epoch_ttl_ms,
    evaluate_slow_clock,
    expire_temporary_promotions,
)
from cyt.tiers.models import (
    EntityKind,
    EntityTierState,
    EntityTierView,
    EpochState,
    SkillsTierPartition,
    Tier,
    TierProject,
    TierSnapshot,
    TierTransition,
    ToolsTierApplyResult,
)
from cyt.tiers.status_detail import (
    build_combined_histogram,
    build_kind_detail,
    config_summary,
    effective_tier_for,
)
from cyt.tiers.store import TierStore
from cyt.tiers.wake import (
    evaluate_fast_sleep,
    fast_promote_on_optional_use,
    fast_promote_on_tool_use,
)

logger = logging.getLogger(__name__)

_manager_lock = threading.RLock()
_managers: dict[str, TierManager] = {}


class NoOpTierManager:
    """Tier manager used when no project root is resolved."""

    project: TierProject | None = None
    is_noop = True
    _states: dict[tuple[str, str], EntityTierState]
    _epoch: EpochState

    def __init__(self) -> None:
        self._states = {}
        self._epoch = EpochState(
            epoch_id=0,
            epoch_start_ms=0,
            last_request_ms=0,
            wake_cycle_id=0,
        )

    def begin_request_cycle(self, config: dict[str, Any]) -> None:
        return

    def end_request_cycle(self) -> None:
        return

    def close(self) -> None:
        return

    def flush_pending(self, *, force: bool = False) -> bool:
        return False

    def snapshot_tools(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="tool")
        return TierSnapshot(
            project=None,
            epoch_id=0,
            epoch_start_ms=0,
            wake_cycle_id=0,
            entities={},
            mode=cfg.mode,
        )

    def snapshot_skills(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="skill")
        return TierSnapshot(
            project=None,
            epoch_id=0,
            epoch_start_ms=0,
            wake_cycle_id=0,
            entities={},
            mode=cfg.mode,
        )

    def apply_tools(
        self,
        tools: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> ToolsTierApplyResult:
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

    def record_tool_attempt(
        self,
        tool: dict[str, Any],
        *,
        config: dict[str, Any],
        success: bool,
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

    def purge_inactive_tool_sources(self, config: dict[str, Any]) -> None:
        del config
        return

    def status(
        self,
        config: dict[str, Any] | None = None,
        *,
        agent: str | None = None,
        filter_by_permissions: bool = True,
    ) -> dict[str, Any]:
        del config, agent, filter_by_permissions
        empty_kind = {
            "histogram": {f"T{i}": 0 for i in range(5)},
            "by_tier": {f"T{i}": [] for i in range(5)},
        }
        return {
            "project_id": None,
            "root_path": None,
            "histogram": {},
            "epoch_id": 0,
            "wake_cycle_id": 0,
            "epoch_timeout_seconds": 0,
            "epoch_remaining_seconds": 0,
            "epoch_start_ms": 0,
            "last_request_ms": 0,
            "tools": empty_kind,
            "skills": empty_kind,
        }


_NOOP_MANAGER = NoOpTierManager()


class TierManager:
    is_noop = False

    def __init__(self, root_path: Path, db_path: str) -> None:
        self._store = TierStore.open(db_path)
        project_id = self._store.get_or_create_project(str(root_path))
        self.project = TierProject(project_id=project_id, root_path=root_path)
        from cyt.hook.active_workspace import touch_active_workspace

        touch_active_workspace("cursor", root_path)
        self._states = self._store.load_entity_states(self.project)
        removed_skill_ids, updated_skill_states = normalize_skill_entity_states(self._states)
        if removed_skill_ids or updated_skill_states:
            for entity_id in removed_skill_ids:
                self._store.delete_entity_state(
                    self.project,
                    kind=EntityKind.SKILL,
                    entity_id=entity_id,
                )
            for state in updated_skill_states:
                self._store.upsert_entity_state(self.project, state)
        removed_tool_ids, updated_tool_states = normalize_tool_entity_states(self._states)
        if removed_tool_ids or updated_tool_states:
            for entity_id in removed_tool_ids:
                self._store.delete_entity_state(
                    self.project,
                    kind=EntityKind.TOOL,
                    entity_id=entity_id,
                )
            for state in updated_tool_states:
                self._store.upsert_entity_state(self.project, state)
        self._purged_tool_scope: tuple[frozenset[str], frozenset[str] | None] | None = None
        self._epoch = self._store.load_epoch_state(self.project)
        self._snapshot_tools: TierSnapshot | None = None
        self._snapshot_skills: TierSnapshot | None = None
        self._state_lock = threading.RLock()
        self._pending_flush = False
        self._last_injected_tools: set[str] = set()
        self._last_injected_skills: set[str] = set()
        self._request_cycle_open = False
        self._cycle_selected: set[tuple[str, str]] = set()
        self._cycle_used: set[tuple[str, str]] = set()
        self._cycle_shadow: set[tuple[str, str]] = set()
        self._rebuild_snapshots(mode=TierMode.SHADOW)

    def _reconcile_ephemeral_states(self) -> None:
        from cyt.tiers.adapters.skills import is_ephemeral_skill_path

        removed: list[tuple[str, str]] = []
        with self._state_lock:
            for key, state in list(self._states.items()):
                if is_ephemeral_skill_path(state.entity_id):
                    removed.append(key)
            for kind, entity_id in removed:
                self._states.pop((kind, entity_id), None)
        for kind, entity_id in removed:
            self._store.delete_entity_state(self.project, kind=kind, entity_id=entity_id)

    def refresh_states_from_store(self) -> None:
        """Merge persisted tier rows from SQLite into memory (for CLI status reporting)."""
        persisted = self._store.load_entity_states(self.project)
        with self._state_lock:
            for key, state in persisted.items():
                existing = self._states.get(key)
                if existing is None or state.stats.last_seen_ms >= existing.stats.last_seen_ms:
                    self._states[key] = state

    def close(self) -> None:
        self.flush_pending(force=True)
        self._store.close()

    def flush_pending(self, *, force: bool = False) -> bool:
        self._reconcile_ephemeral_states()
        with self._state_lock:
            if not force and not self._pending_flush:
                return False
            states = list(self._states.values())
            epoch = self._epoch
            self._pending_flush = False
        for state in states:
            self._store.upsert_entity_state(self.project, state)
        self._store.save_epoch_state(self.project, epoch)
        return True

    def _mark_dirty(self) -> None:
        with self._state_lock:
            self._pending_flush = True

    def _finish_record(self, config: dict[str, Any], *, sync_flush: bool = False) -> None:
        self._mark_dirty()
        if sync_flush or tier_disk_flush_seconds(config) <= 0:
            self.flush_pending(force=True)

    def _workspace_scoped_config(self, config: dict[str, Any]) -> dict[str, Any]:
        from cyt.hook.workspace_config import set_hook_workspace_in_config

        return set_hook_workspace_in_config(config, self.project.root_path)

    def purge_inactive_tool_sources(self, config: dict[str, Any]) -> None:
        from cyt.tiers.adapters.tools import (
            configured_tool_catalog_sources,
            purge_stale_tool_entity_states,
            resolve_tracked_catalog_entity_ids,
        )

        scoped = self._workspace_scoped_config(config)
        allowed = configured_tool_catalog_sources(scoped)
        catalog_entity_ids = resolve_tracked_catalog_entity_ids(scoped, blocking=True)
        scope_key = (allowed, catalog_entity_ids)
        if self._purged_tool_scope == scope_key:
            return
        with self._state_lock:
            removed = purge_stale_tool_entity_states(
                self._states,
                allowed_sources=allowed,
                catalog_entity_ids=catalog_entity_ids,
            )
        if removed:
            for entity_id in removed:
                self._store.delete_entity_state(
                    self.project,
                    kind=EntityKind.TOOL,
                    entity_id=entity_id,
                )
        self._purged_tool_scope = scope_key

    def _ensure_state(
        self,
        kind: str,
        entity_id: str,
        *,
        doc_id: str | None = None,
    ) -> EntityTierState | None:
        if kind == EntityKind.SKILL:
            tier_key = tier_entity_id_for_skill(entity_id, doc_id=doc_id)
            if tier_key is None:
                return None
            entity_id = tier_key
        elif kind == EntityKind.TOOL:
            entity_id = canonical_tool_entity_id(entity_id)
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
            wake_cycle_id=self._epoch.wake_cycle_id,
            entities=entities,
            mode=cfg.mode,
        )

    def _rebuild_snapshots(self, *, mode: TierMode) -> None:
        self._snapshot_tools = TierSnapshot(
            project=self.project,
            epoch_id=self._epoch.epoch_id,
            epoch_start_ms=self._epoch.epoch_start_ms,
            wake_cycle_id=self._epoch.wake_cycle_id,
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
            mode=mode,
        )
        self._snapshot_skills = TierSnapshot(
            project=self.project,
            epoch_id=self._epoch.epoch_id,
            epoch_start_ms=self._epoch.epoch_start_ms,
            wake_cycle_id=self._epoch.wake_cycle_id,
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
            mode=mode,
        )

    def snapshot_tools(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="tool")
        self._rebuild_snapshots(mode=cfg.mode)
        assert self._snapshot_tools is not None
        return self._snapshot_tools

    def snapshot_skills(self, config: dict[str, Any]) -> TierSnapshot:
        cfg = tier_section_config(config, kind="skill")
        self._rebuild_snapshots(mode=cfg.mode)
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
        return apply_tool_tiers(
            tools,
            tier_for_tool=tier_map,
            apply=tiers_apply(config, kind="tool"),
        )

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

    @staticmethod
    def _touch_stats_request(state: EntityTierState) -> None:
        state.stats.requests_since_decay += 1

    def _clear_cycle_activity(self) -> None:
        self._cycle_selected.clear()
        self._cycle_used.clear()
        self._cycle_shadow.clear()

    def _mark_cycle_selected(self, kind: str, entity_id: str) -> None:
        self._cycle_selected.add((kind, entity_id))

    def _mark_cycle_used(self, kind: str, entity_id: str) -> None:
        self._cycle_used.add((kind, entity_id))

    def _mark_cycle_shadow(self, kind: str, entity_id: str) -> None:
        self._cycle_shadow.add((kind, entity_id))

    def _evaluate_fast_sleep_all(self, cfg: TierSectionConfig) -> None:
        wake_cycle_id = self._epoch.wake_cycle_id
        for key, state in self._states.items():
            if state.effective_tier != Tier.COLD:
                continue
            evaluate_fast_sleep(
                state,
                cfg=cfg,
                wake_cycle_id=wake_cycle_id,
                had_selection=key in self._cycle_selected,
                had_use=key in self._cycle_used,
                had_shadow=key in self._cycle_shadow,
            )

    def begin_request_cycle(self, config: dict[str, Any]) -> None:
        if not (tiers_active(config, kind="tool") or tiers_active(config, kind="skill")):
            return
        with self._state_lock:
            if self._request_cycle_open:
                return
            cfg = tier_section_config(config, kind="tool")
            self._evaluate_fast_sleep_all(cfg)
            self._epoch.wake_cycle_id += 1
            self._clear_cycle_activity()
            self._request_cycle_open = True
            # Persist wake cycle immediately so tiers stats / other processes see it
            # without waiting for deferred entity-stat flushes.
            self._store.save_epoch_state(self.project, self._epoch)
            self._mark_dirty()

    def end_request_cycle(self) -> None:
        with self._state_lock:
            self._request_cycle_open = False

    def _advance_epoch_if_age_expired(self, config: dict[str, Any]) -> bool:
        """Roll slow-clock epoch when age TTL elapsed (ignores idle-gap-only expiry)."""
        tool_cfg = tier_section_config(config, kind="tool")
        now_ms = int(time.time() * 1000)
        if not epoch_age_expired(now_ms=now_ms, epoch=self._epoch, cfg=tool_cfg):
            return False
        self._run_epoch(config)
        return True

    def _touch_request(self, config: dict[str, Any]) -> bool:
        """Update epoch bookkeeping. Returns True when a sync epoch flush already ran."""
        now_ms = int(time.time() * 1000)
        tool_cfg = tier_section_config(config, kind="tool")
        self._decay_all(tool_cfg)
        self._epoch.last_request_ms = now_ms
        if epoch_boundary(now_ms=now_ms, epoch=self._epoch, cfg=tool_cfg):
            self._run_epoch(config)
            return True
        expire_temporary_promotions(self._states, now_ms=now_ms)
        return False

    def _run_epoch(self, config: dict[str, Any]) -> None:
        cfg = tier_section_config(config, kind="tool")
        now_ms = int(time.time() * 1000)
        transitions: list[TierTransition] = []
        transitions.extend(
            crystallize_successful_tool_promotions_at_epoch(
                self._states,
                epoch=self._epoch,
            ),
        )
        transitions.extend(expire_temporary_promotions(self._states, now_ms=now_ms))
        transitions.extend(evaluate_slow_clock(self._states, cfg=cfg, epoch=self._epoch))
        if transitions:
            self._store.append_epoch_log(
                self.project,
                epoch_id=self._epoch.epoch_id,
                transitions=transitions,
            )
            if cfg.mode == TierMode.SHADOW:
                logger.debug(
                    "tier shadow epoch %s transitions: %d",
                    self._epoch.epoch_id,
                    len(transitions),
                )
        for state in list(self._states.values()):
            state.stats.epoch_used = 0.0
            state.stats.epoch_attempts = 0.0
        for state in list(self._states.values()):
            self._store.upsert_entity_state(self.project, state)
        self._epoch.epoch_id += 1
        self._epoch.epoch_start_ms = now_ms
        self._epoch.last_request_ms = now_ms
        self._store.save_epoch_state(self.project, self._epoch)
        self._pending_flush = False

    def record_tool_candidates(self, tools: list[dict[str, Any]], config: dict[str, Any]) -> None:
        """Record BM25-eligible exposure (tier pool entering prune), not full catalog."""
        if not tiers_active(config, kind="tool"):
            return
        scoped = self._workspace_scoped_config(config)
        self.purge_inactive_tool_sources(config)
        from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking

        tracked = filter_tools_for_tier_tracking(tools, scoped)
        epoch_ran = False
        with self._state_lock:
            for tool in tracked:
                entity_id = tool_entity_id(tool)
                if not entity_id:
                    continue
                state = self._ensure_state(EntityKind.TOOL, entity_id)
                if state is None:
                    continue
                state.stats.candidates += 1.0
                state.stats.last_seen_ms = int(time.time() * 1000)
                self._mark_cycle_selected(EntityKind.TOOL, entity_id)
            epoch_ran = self._touch_request(config)
            for tool in tracked:
                entity_id = tool_entity_id(tool)
                if not entity_id:
                    continue
                state = self._states.get((EntityKind.TOOL, entity_id))
                if state is not None:
                    self._touch_stats_request(state)
        if not epoch_ran:
            self._finish_record(config)

    def record_tools_injected(
        self,
        tools: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> None:
        """Record tools that survived pruning and were injected into agent context."""
        if not tiers_active(config, kind="tool"):
            return
        scoped = self._workspace_scoped_config(config)
        self.purge_inactive_tool_sources(config)
        from cyt.tiers.adapters.tools import filter_tools_for_tier_tracking

        injected_ids: set[str] = set()
        epoch_ran = False
        with self._state_lock:
            for tool in filter_tools_for_tier_tracking(tools, scoped):
                entity_id = tool_entity_id(tool)
                if not entity_id:
                    continue
                injected_ids.add(entity_id)
                state = self._ensure_state(EntityKind.TOOL, entity_id)
                if state is None:
                    continue
                state.stats.injected += 1.0
                state.stats.last_seen_ms = int(time.time() * 1000)
                self._mark_cycle_selected(EntityKind.TOOL, entity_id)
            self._last_injected_tools = injected_ids
            epoch_ran = self._touch_request(config)
            for entity_id in injected_ids:
                state = self._states.get((EntityKind.TOOL, entity_id))
                if state is not None:
                    self._touch_stats_request(state)
        if not epoch_ran:
            self._finish_record(config)

    def record_tool_attempt(
        self,
        tool: dict[str, Any],
        *,
        config: dict[str, Any],
        success: bool,
        optional_used: bool = False,
    ) -> None:
        if not tiers_active(config, kind="tool"):
            return
        scoped = self._workspace_scoped_config(config)
        self.purge_inactive_tool_sources(config)
        from cyt.tiers.adapters.tools import tool_entity_id, tool_tracked_for_config

        cfg = tier_section_config(config, kind="tool")
        if not tool_tracked_for_config(tool, scoped):
            return
        entity_id = tool_entity_id(tool)
        if not entity_id:
            return
        epoch_ran = False
        sync_flush = False
        with self._state_lock:
            state = self._ensure_state(EntityKind.TOOL, entity_id)
            if state is None:
                return
            state.stats.attempts += 1.0
            state.stats.epoch_attempts += 1.0
            if success:
                state.stats.used += 1.0
                state.stats.epoch_used += 1.0
                self._mark_cycle_used(EntityKind.TOOL, entity_id)
                if entity_id not in self._last_injected_tools:
                    state.stats.used_without_injection += 1.0
                if optional_used:
                    state.stats.optional_used += 1.0
                    fast_promote_on_optional_use(state, cfg=cfg)
                else:
                    fast_promote_on_tool_use(state, cfg=cfg)
            state.stats.last_seen_ms = int(time.time() * 1000)
            epoch_ran = self._touch_request(config)
            self._touch_stats_request(state)
            sync_flush = success and (
                state.temp_promotion_until_ms is not None
                or state.effective_tier > state.stable_tier
            )
        if not epoch_ran:
            self._finish_record(config, sync_flush=sync_flush)

    def record_tool_used(
        self,
        tool: dict[str, Any],
        *,
        config: dict[str, Any],
        optional_used: bool = False,
    ) -> None:
        self.record_tool_attempt(
            tool,
            config=config,
            success=True,
            optional_used=optional_used,
        )

    def record_skill_candidates(self, entries: list[Any], config: dict[str, Any]) -> None:
        """Record tier-eligible skills entering BM25 search, not the full registry."""
        if not tiers_active(config, kind="skill"):
            return
        epoch_ran = False
        with self._state_lock:
            for entry in entries:
                entity_id = skill_entity_id(entry)
                if not entity_id:
                    continue
                state = self._ensure_state(
                    EntityKind.SKILL,
                    entity_id,
                    doc_id=getattr(entry, "doc_id", None),
                )
                if state is None:
                    continue
                state.stats.candidates += 1.0
                state.stats.last_seen_ms = int(time.time() * 1000)
                self._mark_cycle_selected(EntityKind.SKILL, entity_id)
            epoch_ran = self._touch_request(config)
        if not epoch_ran:
            self._finish_record(config)

    def record_skills_injected(
        self,
        matches: list[Any],
        config: dict[str, Any],
    ) -> None:
        """Record skills that survived pruning and were injected into agent context."""
        if not tiers_active(config, kind="skill"):
            return
        injected: set[str] = set()
        epoch_ran = False
        with self._state_lock:
            for match in matches:
                path = getattr(match, "file_path", None) or getattr(match, "source_path", "")
                doc_id = getattr(match, "doc_id", None)
                resolved_doc_id = (
                    str(doc_id) if isinstance(doc_id, str) and doc_id.strip() else None
                )
                entity_id = tier_entity_id_for_skill(str(path), doc_id=resolved_doc_id)
                if not entity_id:
                    continue
                injected.add(entity_id)
                state = self._ensure_state(
                    EntityKind.SKILL,
                    entity_id,
                    doc_id=resolved_doc_id,
                )
                if state is None:
                    continue
                state.stats.injected += 1.0
                state.stats.last_seen_ms = int(time.time() * 1000)
                self._mark_cycle_selected(EntityKind.SKILL, entity_id)
            self._last_injected_skills = injected
            epoch_ran = self._touch_request(config)
        if not epoch_ran:
            self._finish_record(config)

    def record_skill_used(self, entity_id: str, *, config: dict[str, Any]) -> None:
        if not tiers_active(config, kind="skill"):
            return
        canonical_id = tier_entity_id_for_skill(
            entity_id,
            doc_id=resolve_skill_doc_id(entity_id),
        )
        if not canonical_id:
            return
        cfg = tier_section_config(config, kind="skill")
        epoch_ran = False
        sync_flush = False
        with self._state_lock:
            state = self._ensure_state(EntityKind.SKILL, canonical_id)
            if state is None:
                return
            state.stats.used += 1.0
            state.stats.epoch_used += 1.0
            self._mark_cycle_used(EntityKind.SKILL, canonical_id)
            if canonical_id not in self._last_injected_skills:
                state.stats.used_without_injection += 1.0
            fast_promote_on_tool_use(state, cfg=cfg)
            state.stats.last_seen_ms = int(time.time() * 1000)
            epoch_ran = self._touch_request(config)
            self._touch_stats_request(state)
            sync_flush = (
                state.temp_promotion_until_ms is not None
                or state.effective_tier > state.stable_tier
            )
        if not epoch_ran:
            self._finish_record(config, sync_flush=sync_flush)

    def apply_shadow_tool_hits(
        self,
        hits: list[tuple[str, float]],
        config: dict[str, Any],
        *,
        tools_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        from cyt.tiers.adapters.tools import tool_entity_tracked_for_config
        from cyt.tiers.shadow import record_shadow_hits

        self.purge_inactive_tool_sources(config)
        cfg = tier_section_config(config, kind="tool")
        tracked_hits = [
            (entity_id, score)
            for entity_id, score in hits
            if tool_entity_tracked_for_config(entity_id, config)
        ]
        if not tracked_hits:
            return
        with self._state_lock:
            for entity_id, _score in tracked_hits:
                self._mark_cycle_shadow(EntityKind.TOOL, entity_id)
            transitions = record_shadow_hits(
                self._states,
                kind=EntityKind.TOOL,
                hits=tracked_hits,
                cfg=cfg,
                wake_cycle_id=self._epoch.wake_cycle_id,
                tools_by_id=tools_by_id,
            )
        if transitions and cfg.mode == TierMode.SHADOW:
            logger.debug("tool shadow transitions: %d", len(transitions))
        self._finish_record(config)

    def apply_shadow_skill_hits(self, hits: list[tuple[str, float]], config: dict[str, Any]) -> None:
        from cyt.tiers.shadow import record_shadow_hits

        if not tiers_active(config, kind="skill"):
            return
        cfg = tier_section_config(config, kind="skill")
        if not hits:
            return
        with self._state_lock:
            for entity_id, _score in hits:
                self._mark_cycle_shadow(EntityKind.SKILL, entity_id)
            transitions = record_shadow_hits(
                self._states,
                kind=EntityKind.SKILL,
                hits=hits,
                cfg=cfg,
                wake_cycle_id=self._epoch.wake_cycle_id,
            )
        if transitions and cfg.mode == TierMode.SHADOW:
            logger.debug("skill shadow transitions: %d", len(transitions))
        self._finish_record(config)

    def status(
        self,
        config: dict[str, Any] | None = None,
        *,
        agent: str | None = None,
        filter_by_permissions: bool = True,
    ) -> dict[str, Any]:
        if config is not None and tiers_active(config, kind="tool"):
            with self._state_lock:
                self._advance_epoch_if_age_expired(config)

        now_ms = int(time.time() * 1000)

        def effective_fn(state: EntityTierState) -> Tier:
            return effective_tier_for(state, now_ms=now_ms)

        summary = self._store.status_summary(self.project)
        summary["epoch_id"] = self._epoch.epoch_id
        summary["wake_cycle_id"] = self._epoch.wake_cycle_id
        summary["epoch_start_ms"] = self._epoch.epoch_start_ms
        summary["last_request_ms"] = self._epoch.last_request_ms
        tool_cfg = tier_section_config(config, kind="tool") if config is not None else None
        if tool_cfg is not None:
            summary["epoch_timeout_seconds"] = epoch_ttl_ms(tool_cfg) // 1000
            summary["epoch_remaining_seconds"] = (
                epoch_remaining_ms(
                    now_ms=now_ms,
                    epoch=self._epoch,
                    cfg=tool_cfg,
                )
                // 1000
            )
        summary["histogram"] = build_combined_histogram(
            self._states,
            now_ms=now_ms,
            effective_tier_fn=effective_fn,
        )
        if config is not None:
            scoped_config = self._workspace_scoped_config(config)
            from cyt.tiers.adapters.tools import resolve_tracked_catalog_entity_ids
            from cyt.tiers.config import resolve_tier_status_agent
            from cyt.tiers.status_detail import (
                enrich_skill_detail_with_workspace_discoveries,
                enrich_tool_detail_with_catalog_discoveries,
                filter_skill_detail_by_agent,
                filter_skill_detail_by_permissions,
                filter_tool_detail_by_permissions,
            )
            from cyt.tools.master_catalog import get_master_tool_catalog

            catalog_tools = get_master_tool_catalog(scoped_config, blocking=True) or []
            tracked_catalog_ids = resolve_tracked_catalog_entity_ids(
                scoped_config,
                blocking=True,
            )
            tool_cfg = tier_section_config(config, kind="tool")
            skill_cfg = tier_section_config(config, kind="skill")
            with self._state_lock:
                tool_detail = build_kind_detail(
                    self._states,
                    kind=EntityKind.TOOL,
                    cfg=tool_cfg,
                    wake_cycle_id=self._epoch.wake_cycle_id,
                    now_ms=now_ms,
                    effective_tier_fn=effective_fn,
                    config=scoped_config,
                    workspace_root=self.project.root_path,
                    tracked_catalog_entity_ids=tracked_catalog_ids,
                    catalog_tools=catalog_tools,
                )
                tool_detail = enrich_tool_detail_with_catalog_discoveries(
                    tool_detail,
                    states=self._states,
                    cfg=tool_cfg,
                    config=scoped_config,
                    workspace_root=self.project.root_path,
                    catalog_tools=catalog_tools,
                    wake_cycle_id=self._epoch.wake_cycle_id,
                    now_ms=now_ms,
                    effective_tier_fn=effective_fn,
                )
                resolved_agent = resolve_tier_status_agent(
                    config,
                    workspace_root=self.project.root_path,
                    explicit=agent,
                )
                if filter_by_permissions:
                    tool_detail = filter_tool_detail_by_permissions(
                        tool_detail,
                        agent=resolved_agent,
                        workspace_root=self.project.root_path,
                    )
                skill_detail = build_kind_detail(
                    self._states,
                    kind=EntityKind.SKILL,
                    cfg=skill_cfg,
                    wake_cycle_id=self._epoch.wake_cycle_id,
                    now_ms=now_ms,
                    effective_tier_fn=effective_fn,
                    config=config,
                    workspace_root=self.project.root_path,
                )
                skill_detail = enrich_skill_detail_with_workspace_discoveries(
                    skill_detail,
                    states=self._states,
                    cfg=skill_cfg,
                    config=config,
                    workspace_root=self.project.root_path,
                    agent=resolved_agent,
                    wake_cycle_id=self._epoch.wake_cycle_id,
                    now_ms=now_ms,
                    effective_tier_fn=effective_fn,
                )
                skill_detail = filter_skill_detail_by_agent(
                    skill_detail,
                    agent=resolved_agent,
                    config=config,
                    workspace_root=self.project.root_path,
                )
                if filter_by_permissions:
                    skill_detail = filter_skill_detail_by_permissions(
                        skill_detail,
                        agent=resolved_agent,
                        workspace_root=self.project.root_path,
                    )
            summary["agent"] = resolved_agent
            summary["tools"] = {
                "mode": tool_cfg.mode.value,
                "config": config_summary(tool_cfg),
                "tracked_catalog_tool_count": len(tracked_catalog_ids or ()),
                **tool_detail,
            }
            summary["skills"] = {
                "mode": skill_cfg.mode.value,
                "config": config_summary(skill_cfg),
                **skill_detail,
            }
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


def flush_all_tier_managers(*, force: bool = False) -> int:
    with _manager_lock:
        managers = list(_managers.values())
    flushed = 0
    for manager in managers:
        if manager.flush_pending(force=force):
            flushed += 1
    return flushed


def get_tier_manager_for_config(
    config: dict[str, Any],
) -> TierManager | NoOpTierManager:
    """Resolve tier manager from hook workspace embedded in *config*."""
    from cyt.hook.workspace_config import hook_workspace_from_config

    return get_tier_manager(config, workspace=hook_workspace_from_config(config))


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
            db_path = tier_state_db_path(config)
            manager = TierManager(root_path, db_path)
            _managers[key] = manager
        else:
            manager._reconcile_ephemeral_states()
    if tiers_active(config, kind="tool"):
        from cyt.tiers.flush_scheduler import start_tier_flush_scheduler

        start_tier_flush_scheduler(config)
    return manager
