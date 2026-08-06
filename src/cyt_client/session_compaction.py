"""Persist compaction markers from preCompact hooks (stdlib only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from cyt_client.agent import infer_harness_agent
from cyt_client.sessions import append_session_log, read_session_log_file, session_log_path

PRE_COMPACT_EVENTS = frozenset({"preCompact", "PreCompact"})

_COMPACTION_KIND = "compaction"


def is_pre_compact_event(payload: dict[str, Any]) -> bool:
    for layer in (
        payload,
        payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    ):
        if not isinstance(layer, dict):
            continue
        event = layer.get("hook_event_name") or layer.get("hookEventName")
        if isinstance(event, str) and event.strip() in PRE_COMPACT_EVENTS:
            return True
    return False


def _payload_layers(data: dict[str, Any]) -> list[dict[str, Any]]:
    layers = [data]
    nested = data.get("payload")
    if isinstance(nested, dict):
        layers.append(cast(dict[str, Any], nested))
    return layers


def _first_scalar(data: dict[str, Any], *keys: str) -> object | None:
    for layer in _payload_layers(data):
        for key in keys:
            if key in layer:
                return cast(object, layer[key])
    return None


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _optional_bool(value: object | None) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _count_prior_compactions(path: Path) -> int:
    if not path.is_file():
        return 0
    _agent, entries = read_session_log_file(path)
    return sum(1 for entry in entries if entry.get("kind") == _COMPACTION_KIND)


def build_compaction_entry(payload: dict[str, Any]) -> dict[str, Any]:
    path = session_log_path(payload)
    prior = _count_prior_compactions(path) if path is not None else 0
    nested_payload: dict[str, Any] = {}
    trigger = _first_scalar(payload, "trigger")
    if isinstance(trigger, str) and trigger.strip():
        nested_payload["trigger"] = trigger.strip()
    for src_key, dst_key in (
        ("context_usage_percent", "context_usage_percent"),
        ("contextUsagePercent", "context_usage_percent"),
        ("context_tokens", "context_tokens"),
        ("contextTokens", "context_tokens"),
        ("context_window_size", "context_window_size"),
        ("contextWindowSize", "context_window_size"),
        ("message_count", "message_count"),
        ("messageCount", "message_count"),
        ("messages_to_compact", "messages_to_compact"),
        ("messagesToCompact", "messages_to_compact"),
    ):
        parsed = _optional_int(_first_scalar(payload, src_key))
        if parsed is not None:
            nested_payload[dst_key] = parsed
    is_first = _optional_bool(_first_scalar(payload, "is_first_compaction"))
    if is_first is None:
        is_first = _optional_bool(_first_scalar(payload, "isFirstCompaction"))
    nested_payload["is_first_compaction"] = is_first if is_first is not None else prior == 0
    return {
        "kind": _COMPACTION_KIND,
        "key": _COMPACTION_KIND,
        "payload": nested_payload,
    }


def persist_compaction_to_session_log(payload: dict[str, Any]) -> bool:
    path = session_log_path(payload)
    if path is None:
        return False
    entry = build_compaction_entry(payload)
    agent = infer_harness_agent(payload)
    append_session_log(path, [entry], agent=agent)
    return True
