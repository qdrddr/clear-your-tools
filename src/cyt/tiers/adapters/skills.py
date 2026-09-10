"""Skills tier partition and representation helpers."""

from __future__ import annotations

from cyt.common.paths import expand_home_path
from cyt.skills.catalog import SkillEntryRef
from cyt.skills.search import MatchedSkill
from cyt.tiers.models import SkillsTierPartition, Tier


def canonical_skill_entity_id(path: str) -> str:
    """Return a resolved absolute path string for tier entity keys."""
    text = (path or "").strip()
    if not text:
        return text
    if not _looks_like_filesystem_path(text):
        return text
    try:
        return str(expand_home_path(text).resolve())
    except OSError:
        return text


def _looks_like_filesystem_path(text: str) -> bool:
    if text.startswith(("/", "~")):
        return True
    if len(text) > 1 and text[1] == ":":
        return True
    return text.endswith(".md")


def skill_entity_id(entry: SkillEntryRef) -> str:
    raw = entry.source_path or entry.doc_id
    if entry.source_path:
        return canonical_skill_entity_id(entry.source_path)
    return raw


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


def apply_skill_representation(match: MatchedSkill, tier: Tier) -> MatchedSkill:
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
        )
    return match


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
        tier = representation_by_skill.get(match.file_path, Tier.HOT)
        shaped = apply_skill_representation(match, tier) if apply_representation else match
        by_path[match.file_path] = shaped
        order.append(match.file_path)
    for match in t4_direct:
        if match.file_path not in by_path:
            order.append(match.file_path)
        by_path[match.file_path] = match
    return [by_path[path] for path in order if path in by_path]
