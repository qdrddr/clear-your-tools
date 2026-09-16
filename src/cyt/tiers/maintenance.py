"""Background retention and cleanup for tier state database."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

from cyt.db.maintenance import DbTableMaintenanceResult
from cyt.tiers.config import tier_state_db_path, tiers_active
from cyt.tiers.retention_config import tier_retention_config
from cyt.tiers.store import TierStore

logger = logging.getLogger(__name__)

_INCOMPLETE_CATALOG_MIN_RATIO = 0.25
_INCOMPLETE_CATALOG_MIN_COUNT = 10

_scheduler_lock = threading.RLock()


def _catalog_snapshot_safe_for_stale_purge(
    *,
    catalog_entity_ids: frozenset[str],
    persisted_tool_count: int,
    catalog_rebuild_in_progress: bool,
) -> bool:
    """Return False when the hook catalog looks too incomplete to trust for deletes."""
    if catalog_rebuild_in_progress:
        return False
    if persisted_tool_count <= 0:
        return True
    catalog_size = len(catalog_entity_ids)
    if catalog_size >= persisted_tool_count:
        return True
    floor = max(
        _INCOMPLETE_CATALOG_MIN_COUNT,
        int(persisted_tool_count * _INCOMPLETE_CATALOG_MIN_RATIO),
    )
    return catalog_size >= floor


def _maybe_purge_stale_catalog_tools(
    store: TierStore,
    *,
    config: dict[str, Any],
    allowed_sources: frozenset[str],
    catalog_entity_ids: frozenset[str] | None,
) -> int:
    if not catalog_entity_ids:
        return 0
    from cyt.tools.master_catalog import master_catalog_rebuild_in_progress

    persisted_tool_count = store.count_catalog_tool_entities(allowed_sources=allowed_sources)
    if not _catalog_snapshot_safe_for_stale_purge(
        catalog_entity_ids=catalog_entity_ids,
        persisted_tool_count=persisted_tool_count,
        catalog_rebuild_in_progress=master_catalog_rebuild_in_progress(config),
    ):
        logger.warning(
            "skipping stale catalog tool purge: catalog=%d persisted=%d rebuild_in_progress=%s",
            len(catalog_entity_ids),
            persisted_tool_count,
            master_catalog_rebuild_in_progress(config),
        )
        return 0
    return store.purge_stale_catalog_tool_entities(
        allowed_sources=allowed_sources,
        catalog_entity_ids=catalog_entity_ids,
    )
_last_maintenance_start = 0.0
_maintenance_in_progress = False


def _maintenance_marker_path(config: dict[str, Any]) -> Path:
    db_path = Path(tier_state_db_path(config)).expanduser()
    return db_path.parent / ".tier_db_maintenance_last_run"


def maintenance_ran_today(config: dict[str, Any], *, today: date | None = None) -> bool:
    marker = _maintenance_marker_path(config)
    if not marker.is_file():
        return False
    if today is None:
        today = date.today()
    try:
        stored = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return stored == today.isoformat()


def _record_maintenance_run(config: dict[str, Any], *, today: date | None = None) -> None:
    if today is None:
        today = date.today()
    marker = _maintenance_marker_path(config)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(today.isoformat(), encoding="utf-8")


def maybe_run_tier_maintenance_on_stats_query(
    config: dict[str, Any],
) -> DbTableMaintenanceResult | None:
    """Run tier DB maintenance at most once per calendar day (e.g. from ``tiers stats``)."""
    retention = tier_retention_config(config)
    if not retention.enabled or not tiers_active(config, kind="tool"):
        return None
    if maintenance_ran_today(config):
        return None
    result = run_tier_state_maintenance(config)
    _record_maintenance_run(config)
    return result


def run_tier_state_maintenance(
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    vacuum: bool | None = None,
) -> DbTableMaintenanceResult:
    retention = tier_retention_config(config)
    result = DbTableMaintenanceResult(dry_run=dry_run)
    if not retention.enabled:
        return result

    db_path = tier_state_db_path(config)
    store = TierStore.open(db_path)
    try:
        from cyt.tiers.manager import flush_all_tier_managers

        flush_all_tier_managers(force=True)

        now_ms = int(time.time() * 1000)
        idle_cutoff_ms = now_ms - retention.entity_max_idle_days * 86400 * 1000
        age_cutoff_ms = now_ms - retention.epoch_log_max_age_days * 86400 * 1000

        if dry_run:
            result.updated["entity_stats_decay"] = store.count_stats_needing_decay()
            result.deleted["dormant_entities"] = store.count_dormant_entities(
                idle_cutoff_ms=idle_cutoff_ms,
                counter_floor=retention.entity_counter_floor,
            )
            result.deleted["epoch_log"] = store.count_epoch_log_prunable(
                max_age_ms=age_cutoff_ms,
                max_entries=retention.epoch_log_max_entries,
            )
            return result

        if retention.apply_decay_on_maintenance:
            result.updated["entity_stats_decay"] = store.apply_stat_decay(
                request_half_life=retention.request_half_life,
            )

        from cyt.tiers.adapters.tools import (
            configured_tool_catalog_sources,
            resolve_tracked_catalog_entity_ids,
        )
        from cyt.tiers.manager import _managers

        allowed_sources = configured_tool_catalog_sources(config)
        # Require a complete catalog snapshot before deleting persisted tool rows.
        # Non-blocking reads can return a partial SWR cache and mass-delete valid tiers.
        catalog_entity_ids = resolve_tracked_catalog_entity_ids(config, blocking=True)
        for manager in list(_managers.values()):
            purge = getattr(manager, "purge_inactive_tool_sources", None)
            if callable(purge):
                purge(config)
        flush_all_tier_managers(force=True)
        result.deleted["stale_catalog_tools"] = _maybe_purge_stale_catalog_tools(
            store,
            config=config,
            allowed_sources=allowed_sources,
            catalog_entity_ids=catalog_entity_ids,
        )
        result.deleted["dormant_entities"] = store.prune_dormant_entities(
            idle_cutoff_ms=idle_cutoff_ms,
            counter_floor=retention.entity_counter_floor,
        )
        result.deleted["epoch_log"] = store.prune_epoch_log(
            max_age_ms=age_cutoff_ms,
            max_entries=retention.epoch_log_max_entries,
        )

        should_vacuum = retention.vacuum_after_maintenance if vacuum is None else vacuum
        if should_vacuum and result.total_deleted() > 0:
            store.vacuum()
            result.vacuumed = True
    except Exception as exc:
        logger.warning("tier state maintenance failed: %s", exc)
    finally:
        store.close()
    return result


def schedule_tier_state_maintenance_if_due(config: dict[str, Any]) -> None:
    """Run tier DB maintenance when the configured interval has elapsed."""
    retention = tier_retention_config(config)
    if not retention.enabled or not tiers_active(config, kind="tool"):
        return
    if retention.maintenance_interval_seconds <= 0:
        return

    global _last_maintenance_start, _maintenance_in_progress

    now = time.monotonic()
    with _scheduler_lock:
        if _maintenance_in_progress:
            return
        if _last_maintenance_start != 0.0 and (
            now - _last_maintenance_start < retention.maintenance_interval_seconds
        ):
            return
        _last_maintenance_start = now
        _maintenance_in_progress = True

    def _wrapper() -> None:
        global _maintenance_in_progress
        try:
            run_tier_state_maintenance(config)
        except Exception as exc:
            logger.warning("scheduled tier state maintenance failed: %s", exc)
        finally:
            _maintenance_in_progress = False

    threading.Thread(
        target=_wrapper,
        name="cyt-tier-db-maintenance",
        daemon=True,
    ).start()


def reset_tier_maintenance_scheduler_for_tests() -> None:
    global _last_maintenance_start, _maintenance_in_progress

    with _scheduler_lock:
        _last_maintenance_start = 0.0
        _maintenance_in_progress = False
