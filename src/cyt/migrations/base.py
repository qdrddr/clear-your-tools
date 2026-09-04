"""Shared helpers for config.yaml schema migrations."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

ConfigScope = Literal["global", "workspace"]
AppliesTo = Literal["global", "workspace", "both"]


class ConfigMigrationFn(Protocol):
    def __call__(self, cfg: dict[str, Any], *, scope: ConfigScope) -> dict[str, Any]: ...


class RevisionModule(Protocol):
    revision: str
    down_revision: str
    applies_to: AppliesTo
    upgrade: ConfigMigrationFn
    downgrade: ConfigMigrationFn


BASELINE_REVISION = "000_baseline"
CYT_META_KEY = "cyt"
SCHEMA_VERSION_KEY = "schema_version"
MIGRATED_AT_KEY = "migrated_at"


def deep_copy_config(cfg: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(cfg)


def get_path(cfg: dict[str, Any], *keys: str) -> object | None:
    node: object = cfg
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def ensure_dict(cfg: dict[str, Any], *keys: str) -> dict[str, Any]:
    node = cfg
    for key in keys:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    return node


def set_path(cfg: dict[str, Any], value: object, *keys: str) -> None:
    if not keys:
        return
    node = cfg
    for key in keys[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[keys[-1]] = value


def pop_path(cfg: dict[str, Any], *keys: str) -> object | None:
    if not keys:
        return None
    node: object = cfg
    for key in keys[:-1]:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, dict):
        return None
    return cast(object | None, node.pop(keys[-1], None))


def move_path(
    cfg: dict[str, Any],
    src_keys: tuple[str, ...],
    dst_keys: tuple[str, ...],
    *,
    overwrite: bool = False,
) -> bool:
    """Move a nested value from *src_keys* to *dst_keys* when destination is absent."""
    value = get_path(cfg, *src_keys)
    if value is None:
        return False
    if not overwrite and get_path(cfg, *dst_keys) is not None:
        return False
    set_path(cfg, copy.deepcopy(value), *dst_keys)
    pop_path(cfg, *src_keys)
    return True


def read_schema_version(cfg: dict[str, Any]) -> str:
    cyt = cfg.get(CYT_META_KEY)
    if isinstance(cyt, dict):
        version = cyt.get(SCHEMA_VERSION_KEY)
        if isinstance(version, str) and version.strip():
            return version.strip()
    return BASELINE_REVISION


def set_schema_stamp(cfg: dict[str, Any], revision: str) -> None:
    cyt = ensure_dict(cfg, CYT_META_KEY)
    cyt[SCHEMA_VERSION_KEY] = revision
    cyt[MIGRATED_AT_KEY] = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def applies_to_scope(applies_to: AppliesTo, scope: ConfigScope) -> bool:
    if applies_to == "both":
        return True
    return applies_to == scope


def normalize_permission_entry(item: object) -> str | None:
    """Normalize a YAML deny/allow list item to a cyt permission string."""
    if isinstance(item, dict):
        if len(item) != 1:
            return None
        key, value = next(iter(item.items()))
        key_text = str(key).strip().lower()
        value_text = str(value or "").strip()
        if not value_text:
            return None
        if key_text == "path":
            return f"path:{value_text}"
        if key_text == "name":
            return value_text
        return None
    text = str(item or "").strip()
    return text or None


def normalize_permission_list(raw: object) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    items: list[str] = []
    for item in raw:
        text = normalize_permission_entry(item)
        if text:
            items.append(text)
    return items
