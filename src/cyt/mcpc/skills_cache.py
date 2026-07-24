"""SWR warm cache for MCPC skills and resources snapshots."""

from __future__ import annotations

import copy
import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, cast

from cyt.mcpc.catalog import _cache_key_for_config, _McpcCacheKey
from cyt.mcpc.cli import (
    clear_session_capabilities_cache,
    mcpc_available,
    run_mcpc,
    run_mcpc_json,
    session_supports_capability,
)
from cyt.mcpc.runtime import (
    load_config,
    uses_mcpc_tool_catalog,
)
from cyt.mcpc.session_health import eligible_session_names

logger = logging.getLogger(__name__)


@dataclass
class McpcSkillsSnapshot:
    own_skill: dict[str, str] | None = None
    in_session: list[dict[str, str]] = field(default_factory=list)
    resources: list[dict[str, str]] = field(default_factory=list)
    updated_at: float = 0.0


_snapshot_lock = threading.Lock()
_snapshots: dict[_McpcCacheKey, McpcSkillsSnapshot] = {}


def clear_mcpc_skills_cache() -> None:
    with _snapshot_lock:
        _snapshots.clear()
    clear_session_capabilities_cache()


def _get_snapshot_state(cache_key: _McpcCacheKey) -> McpcSkillsSnapshot:
    with _snapshot_lock:
        state = _snapshots.get(cache_key)
        if state is None:
            state = McpcSkillsSnapshot()
            _snapshots[cache_key] = state
        return state


def _snapshot_copy(state: McpcSkillsSnapshot) -> McpcSkillsSnapshot:
    with _snapshot_lock:
        return copy.deepcopy(state)


def _apply_snapshot(
    cache_key: _McpcCacheKey,
    *,
    own_skill: dict[str, str] | None,
    in_session: list[dict[str, str]],
    resources: list[dict[str, str]],
) -> None:
    state = _get_snapshot_state(cache_key)
    with _snapshot_lock:
        state.own_skill = copy.deepcopy(own_skill) if own_skill else None
        state.in_session = copy.deepcopy(in_session)
        state.resources = copy.deepcopy(resources)
        state.updated_at = time.monotonic()


def _inline_source(
    *,
    path: str,
    content: str,
) -> dict[str, str]:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "path": path,
        "content": content,
        "content_sha256": content_hash,
    }


def _text_from_mcp_contents(payload: object | None) -> tuple[str, str]:
    if not isinstance(payload, dict):
        return "", ""
    payload_dict = cast(dict[str, Any], payload)
    contents = payload_dict.get("contents")
    if not isinstance(contents, list) or not contents:
        return "", ""
    first = contents[0]
    if not isinstance(first, dict):
        return "", ""
    text = str(first.get("text") or "").strip()
    mime_type = str(first.get("mimeType") or "").strip()
    return text, mime_type


def _skill_registry_content(
    *,
    session: str,
    name: str,
    uri: str,
    description: str,
    body: str,
) -> str:
    lines = [
        "---",
        f"name: {name}",
    ]
    if description.strip():
        lines.append(f"description: {description.strip()}")
    lines.extend(
        [
            f"mcpc_session: {session}",
            f"mcpc_skill_uri: {uri}",
            f"mcpc_command: mcpc --json {session} skills-get {uri}",
            "---",
            "",
            body.strip(),
        ],
    )
    return "\n".join(lines)


def _resource_registry_content(
    *,
    session: str,
    name: str,
    uri: str,
    description: str,
    mime_type: str,
    body: str,
) -> str:
    lines = [
        "---",
        f"name: {name}",
    ]
    if description.strip():
        lines.append(f"description: {description.strip()}")
    lines.extend(
        [
            f"mcpc_session: {session}",
            "mcpc_kind: resource",
            f"mcpc_uri: {uri}",
            f"mcpc_command: mcpc --json {session} resources-read {uri}",
        ],
    )
    if mime_type.strip():
        lines.append(f"mimeType: {mime_type.strip()}")
    lines.extend(["---", "", body.strip()])
    return "\n".join(lines)


def _fetch_own_skill(executable: str) -> dict[str, str] | None:
    exit_code, stdout, _stderr = run_mcpc(executable, ["help", "--skill"])
    if exit_code != 0:
        return None
    text = stdout.strip()
    if not text:
        return None
    if text.startswith("---"):
        content = text
        if "mcpc_command:" not in content:
            end = content.find("\n---", 3)
            if end != -1:
                header = content[3:end]
                body_start = end + 4
                body = content[body_start:]
                content = f"---\n{header}\nmcpc_command: mcpc help --skill\n---{body}"
    else:
        content = "---\nname: mcpc\nmcpc_command: mcpc help --skill\n---\n\n" + text
    return _inline_source(path="mcpc/help/SKILL.md", content=content)


def _fetch_session_skills(
    executable: str,
    session: str,
) -> list[dict[str, str]]:
    list_payload = run_mcpc_json(
        executable,
        [session, "skills-list"],
        optional_method=True,
    )
    if not isinstance(list_payload, list):
        return []
    sources: list[dict[str, str]] = []
    for raw_item in list_payload:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, Any], raw_item)
        uri = str(item.get("url") or item.get("uri") or "").strip()
        if not uri:
            continue
        name = str(item.get("name") or uri.rsplit("/", 1)[-1] or "skill").strip()
        description = str(item.get("description") or "").strip()
        get_payload = run_mcpc_json(executable, [session, "skills-get", uri])
        body, mime_type = _text_from_mcp_contents(get_payload)
        if not body:
            continue
        if mime_type and mime_type != "text/markdown":
            continue
        content = _skill_registry_content(
            session=session,
            name=name,
            uri=uri,
            description=description,
            body=body,
        )
        safe_name = name.replace("/", "-")
        sources.append(
            _inline_source(
                path=f"mcpc/{session.lstrip('@')}/skills/{safe_name}.md",
                content=content,
            ),
        )
    return sources


def _fetch_session_resources(
    executable: str,
    session: str,
    *,
    allowed_mime_types: set[str],
) -> list[dict[str, str]]:
    if not session_supports_capability(executable, session, "resources"):
        return []
    list_payload = run_mcpc_json(
        executable,
        [session, "resources-list"],
        optional_method=True,
    )
    if not isinstance(list_payload, list):
        return []
    sources: list[dict[str, str]] = []
    for raw_item in list_payload:
        if not isinstance(raw_item, dict):
            continue
        item = cast(dict[str, Any], raw_item)
        uri = str(item.get("uri") or "").strip()
        if not uri:
            continue
        mime_type = str(item.get("mimeType") or "text/markdown").strip()
        if allowed_mime_types and mime_type not in allowed_mime_types:
            continue
        name = str(item.get("name") or uri.rsplit("/", 1)[-1] or "resource").strip()
        description = str(item.get("description") or "").strip()
        read_payload = run_mcpc_json(executable, [session, "resources-read", uri])
        body, read_mime = _text_from_mcp_contents(read_payload)
        if not body:
            continue
        effective_mime = read_mime or mime_type
        if allowed_mime_types and effective_mime not in allowed_mime_types:
            continue
        content = _resource_registry_content(
            session=session,
            name=name,
            uri=uri,
            description=description,
            mime_type=effective_mime,
            body=body,
        )
        safe_name = name.replace("/", "-")
        sources.append(
            _inline_source(
                path=f"mcpc/{session.lstrip('@')}/resources/{safe_name}.md",
                content=content,
            ),
        )
    return sources


def _fetch_skills_snapshot_from_cli(
    executable: str,
    slug: str,
    *,
    config: dict[str, Any],
) -> McpcSkillsSnapshot:
    from cyt.config import (
        mcpc_resources_enabled,
        mcpc_resources_mime_types,
        mcpc_skills_in_session_enabled,
        mcpc_skills_own_enabled,
    )

    own_skill: dict[str, str] | None = None
    in_session: list[dict[str, str]] = []
    resources: list[dict[str, str]] = []

    if mcpc_skills_own_enabled(config):
        own_skill = _fetch_own_skill(executable)

    eligible = eligible_session_names(slug, config=config)
    allowed_mimes = set(mcpc_resources_mime_types(config))

    for session in sorted(eligible):
        if mcpc_skills_in_session_enabled(config):
            try:
                in_session.extend(_fetch_session_skills(executable, session))
            except Exception as exc:
                logger.debug("mcpc session skills fetch failed session=%s: %s", session, exc)
        if mcpc_resources_enabled(config):
            try:
                resources.extend(
                    _fetch_session_resources(
                        executable,
                        session,
                        allowed_mime_types=allowed_mimes,
                    ),
                )
            except Exception as exc:
                logger.debug("mcpc session resources fetch failed session=%s: %s", session, exc)

    return McpcSkillsSnapshot(
        own_skill=own_skill,
        in_session=in_session,
        resources=resources,
        updated_at=time.monotonic(),
    )


def refresh_mcpc_skills_snapshot(
    config: dict[str, Any] | None = None,
) -> None:
    """Background refresh helper for the scheduler."""
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg):
        return
    cache_key = _cache_key_for_config(cfg)
    if not mcpc_available(cache_key.executable):
        logger.warning(
            "mcpc skills refresh skipped; executable unavailable: %s",
            cache_key.executable,
        )
        return
    try:
        snapshot = _fetch_skills_snapshot_from_cli(
            cache_key.executable,
            cache_key.slug,
            config=cfg,
        )
    except Exception as exc:
        logger.warning("mcpc skills snapshot fetch failed slug=%s: %s", cache_key.slug, exc)
        return
    _apply_snapshot(
        cache_key,
        own_skill=snapshot.own_skill,
        in_session=snapshot.in_session,
        resources=snapshot.resources,
    )
    logger.info(
        "mcpc skills snapshot refreshed slug=%s own=%s session_skills=%d resources=%d",
        cache_key.slug,
        bool(snapshot.own_skill),
        len(snapshot.in_session),
        len(snapshot.resources),
    )


def get_mcpc_skills_snapshot(
    config: dict[str, Any] | None = None,
    *,
    blocking: bool = False,
) -> McpcSkillsSnapshot | None:
    """Return the in-memory MCPC skills/resources snapshot (never blocks on hook path)."""
    cfg = config or load_config()
    if not uses_mcpc_tool_catalog(cfg):
        return None
    cache_key = _cache_key_for_config(cfg)
    state = _snapshot_copy(_get_snapshot_state(cache_key))
    if blocking and not state.updated_at:
        refresh_mcpc_skills_snapshot(cfg)
        state = _snapshot_copy(_get_snapshot_state(cache_key))
    from cyt.mcpc.cache_scheduler import start_mcpc_cache_scheduler

    start_mcpc_cache_scheduler(cfg)
    return state
