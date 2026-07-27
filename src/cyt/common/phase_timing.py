"""Wall-clock phase timing for hook/prune pipelines."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

PhaseMetaValue = str | int | float | bool | None


@dataclass
class PhaseRecord:
    name: str
    elapsed_ms: int
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "elapsed_ms": self.elapsed_ms}
        if self.meta:
            out["meta"] = self.meta
        return out


@dataclass
class PhaseTimer:
    phases: list[PhaseRecord] = field(default_factory=list)

    @contextmanager
    def measure(self, name: str, **meta: PhaseMetaValue) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            self.phases.append(
                PhaseRecord(
                    name=name,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    meta=dict(meta),
                ),
            )

    def extend(self, records: list[PhaseRecord]) -> None:
        self.phases.extend(records)

    def merge(self, other: PhaseTimer | None) -> None:
        if other is not None:
            self.phases.extend(other.phases)

    def total_ms(self) -> int:
        return sum(record.elapsed_ms for record in self.phases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ms": self.total_ms(),
            "phases": [record.to_dict() for record in self.phases],
        }


def merge_phase_timings(
    *timers: PhaseTimer | None,
) -> dict[str, Any]:
    merged = PhaseTimer()
    for timer in timers:
        merged.merge(timer)
    return merged.to_dict()


def extend_timing_payload(
    base: dict[str, Any] | None,
    *timers: PhaseTimer | None,
) -> dict[str, Any]:
    """Append timer phases onto an existing timing payload (e.g. hook + gate)."""
    merged = PhaseTimer()
    if base:
        for raw in base.get("phases", []):
            if not isinstance(raw, dict):
                continue
            merged.phases.append(
                PhaseRecord(
                    name=str(raw.get("name", "")),
                    elapsed_ms=int(raw.get("elapsed_ms", 0)),
                    meta=dict(raw.get("meta") or {}),
                ),
            )
    for timer in timers:
        merged.merge(timer)
    return merged.to_dict()
