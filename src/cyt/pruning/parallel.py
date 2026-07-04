"""Concurrent execution helper for pruning work units and remote bulks."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

from cyt.pruning.context import MAX_PRUNE_BATCH_WORKERS

T = TypeVar("T")


def run_parallel[T](
    work: Mapping[str, Callable[[], T]],
    *,
    max_workers: int = MAX_PRUNE_BATCH_WORKERS,
    thread_name_prefix: str = "cyt-prune",
) -> dict[str, T]:
    """Run callables concurrently; preserve keys; propagate the first exception."""
    if not work:
        return {}
    if len(work) == 1:
        key, fn = next(iter(work.items()))
        return {key: fn()}

    worker_count = min(max_workers, len(work))
    results: dict[str, T] = {}
    errors: list[BaseException] = []

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix=thread_name_prefix,
    ) as executor:
        futures = {executor.submit(fn): key for key, fn in work.items()}
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except BaseException as exc:
                errors.append(exc)

    if errors:
        raise errors[0]
    return results
