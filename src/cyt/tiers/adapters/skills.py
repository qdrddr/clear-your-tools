"""Skills tier partition and representation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cyt.pruners.remote import PrunerSettingsCache

from cyt.common.paths import expand_home_path, is_ephemeral_workspace_path
from cyt.skills.catalog import SkillEntryRef, doc_id_from_path
from cyt.skills.frontmatter import skill_name_from_frontmatter
from cyt.skills.search import MatchedSkill
from cyt.tiers.models import EntityTierState, SkillsTierPartition, Tier

_SKILL_DOC_ENTITY_PREFIX = "skill:doc:"
_GENERIC_SKILL_DOC_IDS = frozenset({"skill", "doc"})
_NON_SKILL_SKILL_ENTITY_IDS = frozenset({"executor/execute"})

_EPHEMERAL_SKILL_MARKERS = (
    "/tmp/",
    "/t/tmp/",
    "/private/tmp/",
    "pytest-of-",
    "\\tmp\\",
    "\\temp\\",
)

_SKILL_LOCATION_MARKERS = (
    "/.cursor/skills/",
    "/.agents/skills/",
    "/skills-cursor/",
    "/.claude/skills/",
    "/.codex/skills/",
)


def is_ephemeral_skill_path(path: str) -> bool:
    """True for temp dirs, pytest fixtures, and skinny intercept copies."""
    normalized = path.replace("\\", "/").lower()
    if "/.cyt/skinny/" in normalized:
        return True
    if is_ephemeral_workspace_path(path):
        return True
    if not any(marker in normalized for marker in _EPHEMERAL_SKILL_MARKERS):
        return False
    return not any(marker in normalized for marker in _SKILL_LOCATION_MARKERS)


def stable_skill_doc_entity_id(doc_id: str) -> str:
    doc = (doc_id or "").strip()
    if not doc:
        return ""
    return f"{_SKILL_DOC_ENTITY_PREFIX}{doc}"


def skill_doc_id_from_entity_id(entity_id: str) -> str | None:
    text = (entity_id or "").strip()
    if text.startswith(_SKILL_DOC_ENTITY_PREFIX):
        doc = text.removeprefix(_SKILL_DOC_ENTITY_PREFIX).strip()
        return doc or None
    if text.startswith("skill:"):
        doc = text.removeprefix("skill:").strip()
        return doc or None
    return None


def find_canonical_skill_path_for_doc_id(
    doc_id: str,
    existing_entity_ids: set[str],
) -> str | None:
    """Return a filesystem skill entity id matching *doc_id*, if one exists."""
    target = doc_id.strip().casefold()
    if not target:
        return None
    for entity_id in existing_entity_ids:
        if entity_id.startswith(_SKILL_DOC_ENTITY_PREFIX):
            continue
        if entity_id in _NON_SKILL_SKILL_ENTITY_IDS:
            continue
        resolved = resolve_skill_doc_id(entity_id)
        if resolved and resolved.casefold() == target:
            return entity_id
    return None


def is_stale_skill_doc_entity(entity_id: str, existing_entity_ids: set[str]) -> bool:
    """True for generic doc-id placeholders and duplicate skill:doc rows."""
    doc_id = skill_doc_id_from_entity_id(entity_id)
    if doc_id is None:
        return False
    if doc_id.casefold() in _GENERIC_SKILL_DOC_IDS:
        return True
    canonical = find_canonical_skill_path_for_doc_id(doc_id, existing_entity_ids)
    return canonical is not None and canonical != entity_id


def _looks_like_filesystem_path(text: str) -> bool:
    normalized = text.replace("\\", "/")
    if normalized.startswith(("mcpc/", _SKILL_DOC_ENTITY_PREFIX)):
        return False
    if text.startswith(("/", "~")):
        return True
    if len(text) > 1 and text[1] == ":":
        return True
    return text.endswith(".md")


def canonical_skill_entity_id(path: str) -> str:
    """Return a resolved absolute path string for tier entity keys."""
    text = (path or "").strip()
    if not text:
        return text
    if is_ephemeral_skill_path(text):
        return ""
    if not _looks_like_filesystem_path(text):
        return text.replace("\\", "/")
    try:
        resolved = str(expand_home_path(text).resolve())
    except OSError:
        return text
    if is_ephemeral_skill_path(resolved):
        return ""
    return resolved


def resolve_skill_entity_id(
    path: str,
    *,
    doc_id: str | None = None,
    name: str | None = None,
) -> str:
    """Stable tier key: real skill path, virtual mcpc path, or doc_id fallback."""
    del name
    text = (path or "").strip()
    if text.startswith((_SKILL_DOC_ENTITY_PREFIX, "mcpc/")):
        return text.replace("\\", "/")
    if text and not is_ephemeral_skill_path(text):
        if not _looks_like_filesystem_path(text):
            return text.replace("\\", "/")
        canonical = canonical_skill_entity_id(text)
        if canonical:
            return canonical
    if is_ephemeral_skill_path(text):
        if doc_id:
            return stable_skill_doc_entity_id(doc_id)
        # Never infer doc_id from temp/private paths — only catalog doc_id is allowed.
        return ""
    if doc_id:
        return stable_skill_doc_entity_id(doc_id)
    if text:
        fallback_doc = resolve_skill_doc_id(text) or doc_id_from_path(Path(text))
        if fallback_doc:
            return stable_skill_doc_entity_id(fallback_doc)
    return ""


def tier_entity_id_for_skill(
    path: str,
    *,
    doc_id: str | None = None,
) -> str | None:
    """Persistable tier key only; None when temp/private paths must not be tracked."""
    resolved = resolve_skill_entity_id(path, doc_id=doc_id)
    if not resolved or is_ephemeral_skill_path(resolved):
        return None
    return resolved


def skill_entity_id(entry: SkillEntryRef) -> str:
    return tier_entity_id_for_skill(entry.source_path, doc_id=entry.doc_id) or ""


def skill_match_entity_id(match: MatchedSkill) -> str:
    return tier_entity_id_for_skill(match.file_path, doc_id=match.doc_id) or ""


def _skill_name_from_markdown_file(path: Path) -> str | None:
    try:
        with path.expanduser().open(encoding="utf-8") as handle:
            head = handle.read(8192)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("\n---", 3)
    if end == -1:
        return None
    return skill_name_from_frontmatter(head[: end + 4])


def resolve_skill_frontmatter_name(
    entity_id: str,
    config: dict[str, Any] | None,
    *,
    source_path: str | None = None,
) -> str | None:
    """Return YAML frontmatter ``name`` for a tier skill entity, when discoverable."""
    path_candidates: list[Path] = []
    if source_path:
        path_candidates.append(Path(source_path))
    elif not is_ephemeral_skill_path(entity_id) and entity_id.startswith(("/", "~")):
        path_candidates.append(Path(entity_id))
    if config is not None:
        resolved = source_path or resolve_skill_source_path(entity_id, config)
        if resolved:
            path_candidates.append(Path(resolved))
    seen: set[str] = set()
    for candidate in path_candidates:
        try:
            key = str(candidate.expanduser().resolve())
        except OSError:
            key = str(candidate.expanduser())
        if key in seen:
            continue
        seen.add(key)
        name = _skill_name_from_markdown_file(candidate)
        if name:
            return name
    return None


def resolve_skill_doc_id(entity_id: str) -> str | None:
    """Resolve a tier doc_id from a skill entity key or filesystem path."""
    doc_id = skill_doc_id_from_entity_id(entity_id)
    if doc_id is not None:
        return doc_id
    if entity_id.startswith(("/", "~")) or is_ephemeral_skill_path(entity_id):
        path = Path(entity_id)
        if path.name.lower() in {"skill.md", "skills.md"} and path.parent.name:
            return path.parent.name.replace("\\", "/").lower()
        if is_ephemeral_skill_path(entity_id):
            return None
        return doc_id_from_path(path)
    return None


def _path_matches_doc_id(path: Path, target: str) -> bool:
    target_l = target.lower()
    if doc_id_from_path(path).lower() == target_l:
        return True
    if path.name.lower() in {"skill.md", "skills.md"}:
        parent = path.parent.name.replace("\\", "/").lower()
        if parent and parent.replace("/", "__") == target_l:
            return True
    return False


def skill_path_visible_for_agent(
    source_path: str,
    agent: str,
    *,
    config: dict[str, Any],
    workspace_root: Path | None = None,
) -> bool:
    """True when *source_path* is under a skill root configured for *agent*."""
    from cyt.launch.upstream import parse_agent_name
    from cyt.skills.agents import is_excluded_agent_system_skill
    from cyt.skills.directories import resolve_skill_directories

    try:
        resolved_agent = parse_agent_name(agent)
    except ValueError:
        return True

    text = str(source_path or "").strip()
    if not text:
        return False

    if is_excluded_agent_system_skill(text, active_agent=resolved_agent):
        return False

    try:
        path = Path(text).expanduser().resolve()
    except OSError:
        path = Path(text).expanduser()

    for root in resolve_skill_directories(
        config,
        agent=resolved_agent,
        workspace_root=workspace_root,
        include_platform_defaults=True,
    ):
        try:
            root_resolved = root.resolve()
        except OSError:
            root_resolved = root
        try:
            path.relative_to(root_resolved)
            return True
        except ValueError:
            continue
    return False


def skill_entity_visible_for_agent(
    entity: dict[str, Any],
    agent: str,
    *,
    config: dict[str, Any],
    workspace_root: Path | None = None,
) -> bool:
    """True when a tier status skill row belongs to *agent*'s skill directories."""
    source_path = entity.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        text = source_path.strip()
        if _looks_like_filesystem_path(text) or text.startswith(("/", "~")):
            return skill_path_visible_for_agent(
                text,
                agent,
                config=config,
                workspace_root=workspace_root,
            )

    entity_id = str(entity.get("entity_id") or "").strip()
    if not entity_id:
        return False

    if entity_id.startswith(("skill:", "skill:doc:", "mcpc/")):
        resolved = resolve_skill_source_path(
            entity_id,
            config,
            workspace_root=workspace_root,
        )
        if not resolved:
            return True
        return skill_path_visible_for_agent(
            resolved,
            agent,
            config=config,
            workspace_root=workspace_root,
        )

    if is_ephemeral_skill_path(entity_id):
        return False

    return skill_path_visible_for_agent(
        entity_id,
        agent,
        config=config,
        workspace_root=workspace_root,
    )


def skill_lookup_directories(
    config: dict[str, Any],
    *,
    workspace_root: Path | None = None,
) -> list[str]:
    """All configured skill roots resolved for tier path lookup."""
    from cyt.config import inject_via_agents
    from cyt.hook.workspace_config import hook_workspace_from_config
    from cyt.skills.directories import resolve_skill_directories

    root = workspace_root
    if root is None:
        root = hook_workspace_from_config(config)

    dirs: list[str] = []
    seen: set[str] = set()
    for agent in inject_via_agents():
        for path in resolve_skill_directories(
            config,
            agent=agent,
            workspace_root=root,
            include_platform_defaults=True,
        ):
            text = str(path)
            if text in seen:
                continue
            seen.add(text)
            dirs.append(text)
    return dirs


def _find_skill_path_by_doc_id(
    doc_id: str,
    config: dict[str, Any],
    *,
    workspace_root: Path | None = None,
) -> str | None:
    target = doc_id.lower()
    for raw_dir in skill_lookup_directories(config, workspace_root=workspace_root):
        try:
            directory = Path(raw_dir).expanduser()
        except OSError:
            continue
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.md"):
            if not path.is_file():
                continue
            if _path_matches_doc_id(path, target):
                try:
                    return str(path.resolve())
                except OSError:
                    return str(path)
    return None


def resolve_skill_source_path(
    entity_id: str,
    config: dict[str, Any],
    *,
    workspace_root: Path | None = None,
) -> str | None:
    """Best-effort lookup of the on-disk skill path for status display."""
    doc_id = resolve_skill_doc_id(entity_id)
    if doc_id is None:
        if not is_ephemeral_skill_path(entity_id) and entity_id.startswith(("/", "~")):
            return entity_id
        return None
    return _find_skill_path_by_doc_id(doc_id, config, workspace_root=workspace_root)


def _skill_display_from_source_path(source_path: str) -> str | None:
    path = Path(source_path)
    if path.name.lower() in {"skill.md", "skills.md"} and path.parent.name:
        return path.parent.name
    stem = path.stem
    return stem if stem else None


def _skill_display_from_entity_id(entity_id: str) -> str:
    canonical_doc = skill_doc_id_from_entity_id(entity_id)
    if canonical_doc:
        return canonical_doc
    if is_ephemeral_skill_path(entity_id):
        resolved_doc = resolve_skill_doc_id(entity_id)
        if resolved_doc:
            return resolved_doc
    if entity_id.startswith("mcpc/"):
        return Path(entity_id).stem or entity_id
    if ":" in entity_id:
        return entity_id.rsplit(":", 1)[-1]
    return entity_id


def skill_display_name(
    entity_id: str,
    *,
    config: dict[str, Any] | None = None,
    doc_id: str | None = None,
    source_path: str | None = None,
    frontmatter_name: str | None = None,
) -> str:
    if frontmatter_name and frontmatter_name.strip():
        return frontmatter_name.strip()
    if doc_id and doc_id.strip():
        return doc_id.strip()
    if source_path:
        from_path = _skill_display_from_source_path(source_path)
        if from_path:
            return from_path
    return _skill_display_from_entity_id(entity_id)


def _clone_skill_entity_state(canonical: str, state: EntityTierState) -> EntityTierState:
    from cyt.tiers.models import EffectiveStats, EntityKind

    return EntityTierState(
        entity_id=canonical,
        kind=EntityKind.SKILL,
        stable_tier=state.stable_tier,
        effective_tier=state.effective_tier,
        overlap_tier=state.overlap_tier,
        tier_since_epoch=state.tier_since_epoch,
        temp_promotion_until_ms=state.temp_promotion_until_ms,
        wake_lease_until_cycle=state.wake_lease_until_cycle,
        sleep_cooldown_until_cycle=state.sleep_cooldown_until_cycle,
        pipeline=state.pipeline,
        stats=EffectiveStats(
            candidates=state.stats.candidates,
            injected=state.stats.injected,
            used=state.stats.used,
            used_without_injection=state.stats.used_without_injection,
            optional_used=state.stats.optional_used,
            shadow_hits=state.stats.shadow_hits,
            shadow_evaluations=state.stats.shadow_evaluations,
            last_seen_ms=state.stats.last_seen_ms,
            requests_since_decay=state.stats.requests_since_decay,
        ),
    )


def _merge_skill_entity_stats(target: EntityTierState, state: EntityTierState) -> None:
    target.stats.candidates += state.stats.candidates
    target.stats.injected += state.stats.injected
    target.stats.used += state.stats.used
    target.stats.used_without_injection += state.stats.used_without_injection
    target.stats.optional_used += state.stats.optional_used
    target.stats.shadow_hits += state.stats.shadow_hits
    target.stats.shadow_evaluations += state.stats.shadow_evaluations
    target.stats.last_seen_ms = max(
        target.stats.last_seen_ms,
        state.stats.last_seen_ms,
    )


def _reconcile_ephemeral_skill_state(
    states: dict[tuple[str, str], Any],
    key: tuple[str, str],
    state: EntityTierState,
) -> tuple[list[str], set[str]]:
    from cyt.tiers.models import EntityKind

    removed: list[str] = []
    touched: set[str] = set()
    if key[0] != EntityKind.SKILL or not isinstance(state, EntityTierState):
        return removed, touched
    if not is_ephemeral_skill_path(state.entity_id):
        return removed, touched

    doc_id = resolve_skill_doc_id(state.entity_id)
    canonical = resolve_skill_entity_id(state.entity_id, doc_id=doc_id)
    if not canonical or canonical == state.entity_id:
        removed.append(state.entity_id)
        del states[key]
        return removed, touched

    canonical_key = (EntityKind.SKILL, canonical)
    target = states.get(canonical_key)
    if target is None:
        states[canonical_key] = _clone_skill_entity_state(canonical, state)
    else:
        _merge_skill_entity_stats(target, state)
    touched.add(canonical)
    removed.append(state.entity_id)
    del states[key]
    return removed, touched


def _dedupe_skill_entity_states(updated: list[EntityTierState]) -> list[EntityTierState]:
    deduped: list[EntityTierState] = []
    seen: set[str] = set()
    for state in updated:
        if state.entity_id in seen:
            continue
        seen.add(state.entity_id)
        deduped.append(state)
    return deduped


def normalize_skill_entity_states(
    states: dict[tuple[str, str], Any],
) -> tuple[list[str], list[Any]]:
    """Merge legacy ephemeral skill rows onto canonical entity ids."""
    from cyt.tiers.models import EntityKind, EntityTierState

    removed: list[str] = []
    updated: list[EntityTierState] = []
    touched: set[str] = set()

    for key, state in list(states.items()):
        batch_removed, batch_touched = _reconcile_ephemeral_skill_state(states, key, state)
        removed.extend(batch_removed)
        touched.update(batch_touched)

    for key, state in states.items():
        if key[0] != EntityKind.SKILL or not isinstance(state, EntityTierState):
            continue
        if state.entity_id in touched:
            updated.append(state)
    return removed, _dedupe_skill_entity_states(updated)


def prepare_skill_entry_for_tier_search(entry: SkillEntryRef, tier: Tier) -> SkillEntryRef:
    """Trim skill source before BM25/node search: T1 description, T2 headers-only body."""
    from dataclasses import replace

    if tier in {Tier.DORMANT, Tier.EXTRA_HOT, Tier.HOT}:
        return entry
    markdown = str(entry.document.get("markdown") or entry.document.get("content") or "")
    if tier == Tier.COLD:
        trimmed = skill_description_only_markdown(entry)
    elif tier == Tier.ACTIVE:
        trimmed = _headers_only_markdown(markdown, min_headers=6)
    else:
        return entry
    document = dict(entry.document)
    document["markdown"] = trimmed
    document["content"] = trimmed
    return replace(entry, document=document)


def prepare_skill_entries_for_tier_search(
    entries: list[SkillEntryRef],
    tier_by_skill: dict[str, Tier],
) -> list[SkillEntryRef]:
    prepared: list[SkillEntryRef] = []
    for entry in entries:
        entity_id = skill_entity_id(entry)
        tier = tier_by_skill.get(entity_id, Tier.ACTIVE) if entity_id else Tier.ACTIVE
        prepared.append(prepare_skill_entry_for_tier_search(entry, tier))
    return prepared


def partition_skill_entries(
    entries: list[SkillEntryRef],
    *,
    tier_for_skill: dict[str, Tier],
    apply: bool,
) -> SkillsTierPartition:
    search_entries: list[SkillEntryRef] = []
    t4_direct: list[SkillEntryRef] = []
    tier_by_skill: dict[str, Tier] = {}
    representation_by_skill: dict[str, Tier] = {}

    for entry in entries:
        entity_id = skill_entity_id(entry)
        tier = tier_for_skill.get(entity_id, Tier.ACTIVE)
        tier_by_skill[entity_id] = tier
        representation_by_skill[entity_id] = tier
        if not apply:
            search_entries.append(entry)
            continue
        if tier == Tier.DORMANT:
            continue
        if tier == Tier.EXTRA_HOT:
            t4_direct.append(entry)
            continue
        search_entries.append(entry)

    return SkillsTierPartition(
        search_entries=search_entries,
        t4_direct=t4_direct,
        tier_by_skill=tier_by_skill,
        representation_by_skill=representation_by_skill,
    )


def build_t4_skill_match(entry: SkillEntryRef) -> MatchedSkill:
    document = entry.document
    markdown = str(document.get("markdown") or document.get("content") or "")
    name = document.get("name")
    return MatchedSkill(
        doc_id=entry.doc_id,
        file_path=entry.source_path,
        markdown=markdown,
        name=str(name) if name is not None else None,
        score=1.0,
        token_count=len(markdown.split()),
        injection_tier="t4",
    )


def skill_description_only_markdown(entry: SkillEntryRef) -> str:
    document = entry.document
    frontmatter = document.get("frontmatter")
    if isinstance(frontmatter, str) and frontmatter.strip():
        return f"---\n{frontmatter.strip()}\n---\n"
    description = document.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    name = document.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def _injection_tier_for_skill_tier(tier: Tier) -> str:
    from cyt.injection.tier_legend import injection_tier_attr

    return injection_tier_attr(tier) or "t2"


def _executor_skill_match(match: MatchedSkill) -> bool:
    from cyt.skills.executor_skill import (
        EXECUTOR_SKILL_DOC_ID,
        EXECUTOR_SKILL_NAME,
        EXECUTOR_SKILL_PATH,
    )

    path = match.file_path.replace("\\", "/").rstrip("/")
    return (
        match.name == EXECUTOR_SKILL_NAME
        or match.doc_id == EXECUTOR_SKILL_DOC_ID
        or path == EXECUTOR_SKILL_PATH
        or path.endswith(f"/{EXECUTOR_SKILL_PATH}")
    )


def apply_skill_representation(match: MatchedSkill, tier: Tier) -> MatchedSkill:
    if _executor_skill_match(match) and tier < Tier.HOT:
        tier = Tier.HOT
    tier_attr = _injection_tier_for_skill_tier(tier)
    if tier == Tier.COLD:
        body = skill_description_only_markdown_from_match(match)
        return MatchedSkill(
            doc_id=match.doc_id,
            file_path=match.file_path,
            markdown=body,
            name=match.name,
            score=match.score,
            token_count=len(body.split()),
            command=match.command,
            content_hash=match.content_hash,
            injection_tier=tier_attr,
        )
    if tier == Tier.ACTIVE:
        body = _headers_only_markdown(match.markdown, min_headers=6)
        return MatchedSkill(
            doc_id=match.doc_id,
            file_path=match.file_path,
            markdown=body,
            name=match.name,
            score=match.score,
            token_count=len(body.split()),
            command=match.command,
            content_hash=match.content_hash,
            injection_tier=tier_attr,
        )
    if match.injection_tier:
        return match
    return MatchedSkill(
        doc_id=match.doc_id,
        file_path=match.file_path,
        markdown=match.markdown,
        name=match.name,
        score=match.score,
        token_count=match.token_count,
        command=match.command,
        content_hash=match.content_hash,
        injection_tier=tier_attr,
    )


def skill_description_only_markdown_from_match(match: MatchedSkill) -> str:
    text = match.markdown.strip()
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[: end + 4]
    lines = [line for line in text.splitlines() if line.startswith("#")]
    if lines:
        return "\n".join(lines[:3])
    return match.name or match.file_path


def _headers_only_markdown(markdown: str, *, min_headers: int) -> str:
    headers: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headers.append(stripped)
        if len(headers) >= min_headers:
            break
    if not headers:
        return markdown.splitlines()[0] if markdown.splitlines() else markdown
    return "\n".join(headers)


def merge_skill_matches(
    searched: list[MatchedSkill],
    t4_direct: list[MatchedSkill],
    *,
    representation_by_skill: dict[str, Tier],
    apply_representation: bool = True,
) -> list[MatchedSkill]:
    by_path: dict[str, MatchedSkill] = {}
    order: list[str] = []
    for match in searched:
        entity_id = skill_match_entity_id(match)
        tier = representation_by_skill.get(entity_id, Tier.HOT)
        if apply_representation or not match.injection_tier:
            shaped = apply_skill_representation(match, tier)
        else:
            shaped = match
        by_path[match.file_path] = shaped
        order.append(match.file_path)
    for match in t4_direct:
        if match.file_path not in by_path:
            order.append(match.file_path)
        by_path[match.file_path] = match
    return [by_path[path] for path in order if path in by_path]


def resolve_tiered_skill_matches(
    query: str,
    entries: list[SkillEntryRef],
    *,
    config: dict[str, Any],
    max_tokens: int | None = None,
    pruner_settings: PrunerSettingsCache | None = None,
    skip_frontmatter_gate: bool = False,
) -> list[MatchedSkill]:
    """Partition by tier, search eligible pool, and merge T4 direct inject matches."""
    from cyt.hook.workspace_config import hook_workspace_from_config
    from cyt.skills.search import search_skills
    from cyt.tiers.config import tiers_apply
    from cyt.tiers.manager import get_tier_manager

    resolved_entries = list(entries)
    if not skip_frontmatter_gate and query.strip():
        from cyt.skills.search import eligible_skills_after_gate

        resolved_entries = eligible_skills_after_gate(query, resolved_entries, config=config)

    manager = get_tier_manager(config, workspace=hook_workspace_from_config(config))
    partition = manager.partition_skills(resolved_entries, config)
    manager.record_skill_candidates(partition.search_entries, config)

    search_pool = prepare_skill_entries_for_tier_search(
        partition.search_entries,
        partition.tier_by_skill,
    )
    searched = search_skills(
        query,
        search_pool,
        config=config,
        max_tokens=max_tokens,
        pruner_settings=pruner_settings,
        skip_frontmatter_gate=True,
    )
    t4_matches = [build_t4_skill_match(entry) for entry in partition.t4_direct]
    return merge_skill_matches(
        searched,
        t4_matches,
        representation_by_skill=partition.representation_by_skill,
        apply_representation=tiers_apply(config, kind="skill"),
    )
