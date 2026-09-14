"""Background tier statistics disk flush scheduler."""

from __future__ import annotations

import atexit
import logging
import threading
import time
from typing import Any

from cyt.config import load_config
from cyt.tiers.config import tier_disk_flush_seconds, tiers_active

logger = logging.getLogger(__name__)

_scheduler_lock = threading.RLock()
_stop_event = threading.Event()
_thread: threading.Thread | None = None
_flush_in_progress = False
_last_flush_start = 0.0
_atexit_registered = False


def start_tier_flush_scheduler(config: dict[str, Any] | None = None) -> None:
    """Start the background tier flush scheduler when tiers are active."""
    global _thread, _atexit_registered

    cfg = config or load_config()
    if not tiers_active(cfg, kind="tool"):
        return
    if tier_disk_flush_seconds(cfg) <= 0:
        return

    with _scheduler_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop_event.clear()
        _thread = threading.Thread(
            target=_scheduler_loop,
            kwargs={"config": cfg},
            name="cyt-tier-flush",
            daemon=True,
        )
        _thread.start()
        if not _atexit_registered:
            atexit.register(_force_flush_on_exit)
            _atexit_registered = True


def stop_tier_flush_scheduler(*, join_timeout: float = 2.0) -> None:
    """Stop the scheduler (primarily for tests)."""
    _stop_event.set()
    with _scheduler_lock:
        thread = _thread
    if thread is not None:
        thread.join(timeout=join_timeout)


def reset_tier_flush_scheduler_for_tests() -> None:
    """Reset scheduler state between tests."""
    global _thread, _flush_in_progress, _last_flush_start

    stop_tier_flush_scheduler()
    with _scheduler_lock:
        _thread = None
        _flush_in_progress = False
        _last_flush_start = 0.0
        _stop_event.clear()


def _force_flush_on_exit() -> None:
    from cyt.tiers.manager import flush_all_tier_managers

    flush_all_tier_managers(force=True)


def _scheduler_loop(*, config: dict[str, Any]) -> None:
    global _last_flush_start

    interval = tier_disk_flush_seconds(config)
    while not _stop_event.is_set():
        now = time.monotonic()
        if not _flush_in_progress and (
            _last_flush_start == 0.0 or now - _last_flush_start >= interval
        ):
            _last_flush_start = now
            _start_flush_job()
        _stop_event.wait(timeout=0.1)


def _start_flush_job() -> None:
    global _flush_in_progress

    _flush_in_progress = True

    def _wrapper() -> None:
        global _flush_in_progress
        try:
            from cyt.tiers.manager import flush_all_tier_managers

            flush_all_tier_managers(force=False)
        except Exception as exc:
            logger.warning("tier statistics disk flush failed: %s", exc)
        finally:
            _flush_in_progress = False

    threading.Thread(
        target=_wrapper,
        daemon=True,
        name="cyt-tier-flush-job",
    ).start()
