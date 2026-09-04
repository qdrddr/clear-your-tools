"""Per-skill catalog cache under ~/.config/cyt/skills/entries/."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from cyt.cache.policy import cache_policy_for_config
from cyt.common.agents import AgentName
from cyt.common.paths import shorten_home_path
from cyt.config import (
    _cache_settings,
    _config_with_bundled_defaults,
    _default_at,
    _merged_config,
    cache_skills_dir,
    load_config,
    skills_catalog_dir,
    skills_directories,
    skills_index_params_fingerprint,
    skills_pageindex_config,
    skills_pipeline,
)
from cyt.indexer.cache import ensure_skills_registry
from cyt.indexer.pageindex import (
    build_chunk_variant,
    build_page_index_for_file,
    build_skills_index_for_file,
    chunk_variant_valid,
    finalize_skill_document_json,
    load_merged_skill_document_json,
    load_skills_index_from_entry,
    page_index_config_without_chunking,
    repair_skill_variant_chunks,
    update_skill_document_source_path,
)
from cyt.skills.agents import resolve_skills_agent

logger = logging.getLogger(__name__)

_NODES_DIR = "nodes"
_CHUNKS_DIR = "chunks"
_METADATA_FILE = "metadata.json"
_BM25_PIPELINE = "bm25"

_REGISTRY_CACHE: dict[tuple[Any, ...], list[SkillEntryRef]] = {}


def clear_registry_cache() -> None:
    """Drop the in-process skills registry cache (tests and config reload)."""
    _REGISTRY_CACHE.clear()


def _client_skills_cache_fingerprint(skills: list[dict[str, str]]) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for skill in skills:
        path = skill["path"]
        content_hash = hashlib.sha256(skill["content"].encode("utf-8")).hexdigest()
        rows.append((path, content_hash))
    return tuple(sorted(rows))


def _registry_cache_key(
    cfg: dict[str, Any],
    *,
    agent: AgentName | None,
    upstream_kind: str | None,
    client_skills: list[dict[str, str]] | None = None,
) -> tuple[Any, ...]:
    active_agent = resolve_skills_agent(agent=agent, upstream_kind=upstream_kind)
    catalog_root = str(_registry_catalog_root(cfg))
    if client_skills is not None:
        return (
            catalog_root,
            skills_pipeline(cfg),
            skills_index_params_fingerprint(cfg),
            active_agent,
            "client",
            _client_skills_cache_fingerprint(client_skills),
        )

    expanded_dirs = skills_directories(cfg)
    sources: list[tuple[str, int, int]] = []
    for source_path in _walk_skill_md_files(expanded_dirs):
        stat = source_path.stat()
        sources.append((str(source_path.resolve()), stat.st_mtime_ns, stat.st_size))
    return (
        catalog_root,
        skills_pipeline(cfg),
        skills_index_params_fingerprint(cfg),
        active_agent,
        "config",
        tuple(sorted(sources)),
    )


@dataclass(frozen=True)
class SkillEntryRef:
    source_path: str
    doc_id: str
    content_sha256: str
    cache_key: str
    entry_dir: str
    nodes_dir: str
    chunk_dir: str
    bm25_chunk_dir: str
    pipeline: str
    index_params_hash: str
    disk_backed: bool
    document: dict[str, Any]
    memory_index: dict[str, Any] | None = None


def doc_id_from_path(source_path: Path) -> str:
    name = source_path.name
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.replace("/", "__").lower()


def content_sha256_for_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _index_params(config: dict[str, Any] | None) -> dict[str, Any]:
    pageindex = skills_pageindex_config(config) or {}
    return dict(pageindex)


def _nodes_dir(entry_dir: Path) -> Path:
    return entry_dir / _NODES_DIR


def _metadata_path(entry_dir: Path) -> Path:
    return entry_dir / _METADATA_FILE


def _chunk_variant_dir(entry_dir: Path, pipeline: str, params_hash: str) -> Path:
    return entry_dir / _CHUNKS_DIR / pipeline.strip().lower() / params_hash


def _is_frontmatter_node(mapping: dict[str, Any]) -> bool:
    kind = mapping.get("kind")
    if isinstance(kind, str) and kind == "frontmatter":
        return True
    node_id = mapping.get("node_id")
    if node_id == 0 or node_id == "0":
        return True
    return False


def _append_chunk_ids(mapping: dict[str, Any], chunk_ids: list[int]) -> None:
    chunks = mapping.get("chunks")
    if not isinstance(chunks, list):
        return
    for chunk in chunks:
        if isinstance(chunk, dict) and "chunk_id" in chunk:
            chunk_ids.append(int(chunk["chunk_id"]))


def _append_node_ids(mapping: dict[str, Any], node_ids: list[int]) -> None:
    node_id = mapping.get("node_id")
    if node_id is not None:
        try:
            node_ids.append(int(node_id))
        except (TypeError, ValueError):
            pass


def _iter_node_ids_from_structure(structure: object, *, skip_frontmatter: bool) -> list[int]:
    node_ids: list[int] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        mapping = cast(dict[str, Any], node)
        if not skip_frontmatter or not _is_frontmatter_node(mapping):
            _append_node_ids(mapping, node_ids)
        children = mapping.get("nodes")
        if isinstance(children, list):
            for child in children:
                walk(child)

    walk(structure)
    return node_ids


def _iter_content_node_ids(structure: object) -> list[int]:
    return _iter_node_ids_from_structure(structure, skip_frontmatter=True)


def _iter_chunk_ids_from_structure(structure: object, *, skip_frontmatter: bool) -> list[int]:
    chunk_ids: list[int] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        mapping = cast(dict[str, Any], node)
        if not skip_frontmatter or not _is_frontmatter_node(mapping):
            _append_chunk_ids(mapping, chunk_ids)
        children = mapping.get("nodes")
        if isinstance(children, list):
            for child in children:
                walk(child)

    walk(structure)
    return chunk_ids


def _iter_chunk_ids(structure: object) -> list[int]:
    return _iter_chunk_ids_from_structure(structure, skip_frontmatter=False)


def _iter_content_chunk_ids(structure: object) -> list[int]:
    """Collect chunk ids from all nodes except frontmatter (node 0)."""
    return _iter_chunk_ids_from_structure(structure, skip_frontmatter=True)


def _pipeline_uses_nodes_only(pipeline: str) -> bool:
    return pipeline.strip().lower() in ("llm", "rerank")


def _pipeline_materializes_chunks(pipeline: str) -> bool:
    return pipeline.strip().lower() == _BM25_PIPELINE


def _chunk_variants_for_startup(pipeline: str) -> list[str]:
    variants = [_BM25_PIPELINE]
    normalized = pipeline.strip().lower()
    if normalized in ("llm", "rerank") and _pipeline_materializes_chunks(normalized):
        variants.append(normalized)
    return variants


def _normalize_document_path(document: dict[str, Any]) -> dict[str, Any]:
    path = document.get("path")
    if not isinstance(path, str):
        return document
    canonical = shorten_home_path(path)
    if canonical == path:
        return document
    return {**document, "path": canonical}


def _persist_metadata_source_path(entry_dir: Path, source_path: str) -> None:
    metadata_path = _metadata_path(entry_dir)
    if not metadata_path.is_file():
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(metadata, dict):
        return
    canonical = shorten_home_path(source_path)
    if metadata.get("source_path") == canonical:
        return
    metadata["source_path"] = canonical
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _persist_document_path(entry_dir: Path, document: dict[str, Any]) -> dict[str, Any]:
    document = _normalize_document_path(document)
    page_index_path = _nodes_dir(entry_dir) / "page_index.json"
    if page_index_path.is_file():
        page_index_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return document


def _load_document_json(
    entry_dir: Path,
    doc_id: str,
    *,
    chunk_dir: Path | None = None,
) -> dict[str, Any]:
    document = load_merged_skill_document_json(
        str(entry_dir),
        doc_id,
        str(chunk_dir) if chunk_dir is not None else None,
    )
    if not isinstance(document, dict):
        raise ValueError(f"invalid page_index.json for {doc_id}")
    return _normalize_document_path(document)


def _document_from_ref_or_disk(
    ref: dict[str, Any],
    entry_dir: Path,
    doc_id: str,
    *,
    chunk_dir: Path | None = None,
) -> dict[str, Any]:
    """Prefer the Rust-built document when no chunk overlay is required."""
    if chunk_dir is None:
        inline = ref.get("document")
        if isinstance(inline, dict):
            return inline
    return _load_document_json(entry_dir, doc_id, chunk_dir=chunk_dir)


def _read_entry_metadata(entry_dir: Path) -> dict[str, Any] | None:
    path = _metadata_path(entry_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _metadata_matches(
    metadata: dict[str, Any] | None,
    *,
    pipeline: str,
    index_params: dict[str, Any],
    source_path: str,
) -> bool:
    if metadata is None:
        return False
    if metadata.get("pipeline") != pipeline:
        return False
    stored_params = metadata.get("index_params")
    if stored_params != index_params:
        return False
    canonical = shorten_home_path(source_path)
    stored_path = metadata.get("source_path")
    if not isinstance(stored_path, str):
        return False
    return shorten_home_path(stored_path) == canonical


def _ensure_entry_metadata(
    entry_dir: Path,
    doc_id: str,
    *,
    pipeline: str,
    index_params: dict[str, Any],
    source_path: str,
) -> dict[str, Any]:
    metadata = _read_entry_metadata(entry_dir)
    if _metadata_matches(
        metadata,
        pipeline=pipeline,
        index_params=index_params,
        source_path=source_path,
    ):
        return _load_document_json(entry_dir, doc_id)
    document = finalize_skill_document_json(
        str(entry_dir),
        doc_id,
        pipeline=pipeline,
        index_params=index_params,
        source_path=source_path,
    )
    _persist_metadata_source_path(entry_dir, source_path)
    return _persist_document_path(entry_dir, document)


def _build_page_index_memory(
    source_path: Path,
    *,
    pageindex_config: dict[str, Any] | None,
    pipeline: str,
    index_params_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    doc_id = doc_id_from_path(source_path)
    if pipeline.strip().lower() == _BM25_PIPELINE:
        index = build_skills_index_for_file(
            str(source_path),
            config=pageindex_config,
            pipeline=_BM25_PIPELINE,
            params_hash=index_params_hash,
        )
    else:
        nodes_only_config = page_index_config_without_chunking().to_dict()
        if pageindex_config:
            nodes_only_config.update(pageindex_config)
            nodes_only_config["enable_bm25_chunking"] = False
        index = build_page_index_for_file(str(source_path), config=nodes_only_config)
    if not isinstance(index, dict):
        raise ValueError("skills index build returned no index")
    documents = index.get("documents")
    if not isinstance(documents, dict) or doc_id not in documents:
        raise ValueError(f"missing document {doc_id} in memory index")
    document = documents[doc_id]
    if not isinstance(document, dict):
        raise ValueError(f"invalid document for {doc_id}")
    return document, index


def _ensure_chunk_variant(
    entry_dir: Path,
    doc_id: str,
    pipeline: str,
    params_hash: str,
    *,
    pageindex_config: dict[str, Any] | None,
) -> None:
    if chunk_variant_valid(str(entry_dir), doc_id, pipeline, params_hash):
        return
    try:
        repair_skill_variant_chunks(
            str(entry_dir),
            doc_id,
            pipeline,
            params_hash,
            config=pageindex_config,
        )
    except ValueError:
        build_chunk_variant(
            str(entry_dir),
            doc_id,
            pipeline,
            params_hash,
            config=pageindex_config,
        )


def _walk_skill_md_files(directories: list[str]) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.md")):
            if path.is_file():
                files.append(path)
    return files


def load_entry_skills_index(entry: SkillEntryRef) -> dict[str, Any]:
    """Load the skills index dict for an entry (disk or in-memory)."""
    if not entry.disk_backed:
        if entry.memory_index is None:
            raise ValueError(f"memory-backed entry missing index: {entry.doc_id}")
        return entry.memory_index

    chunk_dir = entry.bm25_chunk_dir
    return load_skills_index_from_entry(entry.entry_dir, entry.doc_id, chunk_dir)


def _registry_catalog_root(cfg: dict[str, Any]) -> Path:
    skills_catalog = Path(skills_catalog_dir(cfg))
    default_catalog = Path(str(_default_at("skills", "catalog_dir"))).expanduser()
    if skills_catalog != default_catalog:
        return skills_catalog
    cache = _cache_settings(_merged_config(cfg))
    if cache.get("skills_dir"):
        return cache_skills_dir(cfg)
    return skills_catalog


def _entry_from_rust_ref(
    ref: dict[str, Any],
    source_path: Path,
    *,
    pipeline: str,
    index_params: dict[str, Any],
    index_params_hash: str,
    pageindex_config: dict[str, Any] | None,
) -> SkillEntryRef:
    content_hash = str(ref["content_sha256"])
    doc_id = str(ref["doc_id"])
    entry_dir = Path(str(ref["entry_dir"])).expanduser()
    nodes_dir = _nodes_dir(entry_dir)
    bm25_chunk_dir = _chunk_variant_dir(entry_dir, _BM25_PIPELINE, index_params_hash)
    chunk_dir = (
        _chunk_variant_dir(entry_dir, pipeline, index_params_hash)
        if _pipeline_materializes_chunks(pipeline)
        else bm25_chunk_dir
    )
    disk_backed = bool(ref.get("disk_backed"))
    memory_index: dict[str, Any] | None = None
    document: dict[str, Any]

    if disk_backed:
        for variant_pipeline in _chunk_variants_for_startup(pipeline):
            if variant_pipeline == _BM25_PIPELINE:
                continue
            _ensure_chunk_variant(
                entry_dir,
                doc_id,
                variant_pipeline,
                index_params_hash,
                pageindex_config=pageindex_config,
            )

        merge_chunk_dir = chunk_dir if _pipeline_materializes_chunks(pipeline) else None
        document = _document_from_ref_or_disk(
            ref,
            entry_dir,
            doc_id,
            chunk_dir=merge_chunk_dir,
        )
        if not _metadata_matches(
            _read_entry_metadata(entry_dir),
            pipeline=pipeline,
            index_params=index_params,
            source_path=str(source_path),
        ):
            document = _ensure_entry_metadata(
                entry_dir,
                doc_id,
                pipeline=pipeline,
                index_params=index_params,
                source_path=str(source_path),
            )
    else:
        logger.debug("skills catalog not writable; using in-memory index for %s", source_path)
        document, memory_index = _build_page_index_memory(
            source_path,
            pageindex_config=pageindex_config,
            pipeline=pipeline,
            index_params_hash=index_params_hash,
        )

    canonical_path = shorten_home_path(str(source_path))
    if disk_backed and document.get("path") != canonical_path:
        document = update_skill_document_source_path(
            str(entry_dir),
            doc_id,
            str(source_path),
        )
    document = _persist_document_path(entry_dir, document)
    if disk_backed:
        _persist_metadata_source_path(entry_dir, str(source_path))

    return SkillEntryRef(
        source_path=str(source_path),
        doc_id=doc_id,
        content_sha256=content_hash,
        cache_key=content_hash,
        entry_dir=str(entry_dir),
        nodes_dir=str(nodes_dir),
        chunk_dir=str(chunk_dir.as_posix()),
        bm25_chunk_dir=str(bm25_chunk_dir.as_posix()),
        pipeline=pipeline,
        index_params_hash=index_params_hash,
        disk_backed=disk_backed,
        document=document,
        memory_index=memory_index,
    )


def build_registry(
    config: dict[str, Any] | None = None,
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
    client_skills: list[dict[str, str]] | None = None,
) -> list[SkillEntryRef]:
    """Scan skill sources and return in-memory entry metadata."""
    cfg = _config_with_bundled_defaults(config) if config is not None else load_config()
    cache_key = _registry_cache_key(
        cfg,
        agent=agent,
        upstream_kind=upstream_kind,
        client_skills=client_skills,
    )
    cached = _REGISTRY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    entries = _build_registry_uncached(
        cfg,
        agent=agent,
        upstream_kind=upstream_kind,
        client_skills=client_skills,
    )
    _REGISTRY_CACHE[cache_key] = entries
    return entries


def _build_registry_from_inline_sources(
    cfg: dict[str, Any],
    inline_sources: list[dict[str, str]],
    *,
    original_by_hash: dict[str, Path],
) -> list[SkillEntryRef]:
    """Build a skills registry from inline ``{path, content, content_sha256}`` sources."""
    if not inline_sources:
        return []

    catalog_root = _registry_catalog_root(cfg)
    pipeline = skills_pipeline(cfg)
    index_params = _index_params(cfg)
    pageindex_config = skills_pageindex_config(cfg)
    index_params_hash = skills_index_params_fingerprint(cfg)

    rust_refs = ensure_skills_registry(
        inline_sources,
        str(catalog_root),
        pageindex_config,
        pipeline,
        index_params_hash,
        policy=cache_policy_for_config(cfg),
    )

    entries: list[SkillEntryRef] = []
    for ref in rust_refs:
        content_hash = str(ref["content_sha256"])
        original_path = original_by_hash.get(content_hash)
        if original_path is None:
            continue
        entry = _entry_from_rust_ref(
            ref,
            original_path,
            pipeline=pipeline,
            index_params=index_params,
            index_params_hash=index_params_hash,
            pageindex_config=pageindex_config,
        )
        entries.append(entry)
    return entries


def build_registry_from_inline_sources(
    cfg: dict[str, Any],
    inline_sources: list[dict[str, str]],
    *,
    original_by_hash: dict[str, Path],
) -> list[SkillEntryRef]:
    """Public wrapper for inline skill sources (executor API, cyt-client payloads)."""
    return _build_registry_from_inline_sources(
        cfg,
        inline_sources,
        original_by_hash=original_by_hash,
    )


def _build_registry_from_client_skills(
    cfg: dict[str, Any],
    client_skills: list[dict[str, str]],
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
) -> list[SkillEntryRef]:
    """Build a skills registry from cyt-client payload content instead of config dirs."""
    seen_content: set[str] = set()
    inline_sources: list[dict[str, str]] = []
    original_by_hash: dict[str, Path] = {}

    for skill in client_skills:
        original_path = Path(skill["path"]).expanduser()
        content = skill["content"]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in seen_content:
            continue
        seen_content.add(content_hash)
        canonical_path = str(original_path.resolve())
        inline_sources.append(
            {
                "path": canonical_path,
                "content": content,
                "content_sha256": content_hash,
            },
        )
        original_by_hash[content_hash] = original_path

    return _build_registry_from_inline_sources(
        cfg,
        inline_sources,
        original_by_hash=original_by_hash,
    )


def _filter_agent_system_skills(
    entries: list[SkillEntryRef],
    *,
    active_agent: AgentName | None,
) -> list[SkillEntryRef]:
    if active_agent is None:
        return entries
    from cyt.skills.agents import is_excluded_agent_system_skill

    return [
        entry
        for entry in entries
        if not is_excluded_agent_system_skill(entry.source_path, active_agent=active_agent)
    ]


def _build_registry_uncached(
    cfg: dict[str, Any],
    *,
    agent: AgentName | None = None,
    upstream_kind: str | None = None,
    client_skills: list[dict[str, str]] | None = None,
) -> list[SkillEntryRef]:
    """Build skills registry without the process-level cache."""
    if client_skills is not None:
        client_entries = _build_registry_from_client_skills(
            cfg,
            client_skills,
            agent=agent,
            upstream_kind=upstream_kind,
        )
        from cyt.hook.workspace_config import hook_workspace_from_config
        from cyt.permissions.runtime import filter_skill_entries, resolve_effective_permissions

        active_agent = resolve_skills_agent(agent=agent, upstream_kind=upstream_kind)
        effective = resolve_effective_permissions(config=cfg, agent=active_agent)
        workspace = hook_workspace_from_config(cfg)
        workspace_base = Path(str(workspace)).expanduser() if workspace else None
        return _filter_agent_system_skills(
            filter_skill_entries(
                client_entries,
                effective.skills.deny,
                base=workspace_base,
            ),
            active_agent=active_agent,
        )

    expanded_dirs = skills_directories(cfg)
    active_agent = resolve_skills_agent(agent=agent, upstream_kind=upstream_kind)

    catalog_root = _registry_catalog_root(cfg)
    pipeline = skills_pipeline(cfg)
    index_params = _index_params(cfg)
    pageindex_config = skills_pageindex_config(cfg)
    index_params_hash = skills_index_params_fingerprint(cfg)

    seen_content: set[str] = set()
    source_paths: list[str] = []
    source_by_hash: dict[str, Path] = {}

    for source_path in _walk_skill_md_files(expanded_dirs):
        content_hash = content_sha256_for_file(source_path)
        if content_hash in seen_content:
            continue
        seen_content.add(content_hash)
        source_paths.append(str(source_path))
        source_by_hash[content_hash] = source_path

    rust_refs = ensure_skills_registry(
        source_paths,
        str(catalog_root),
        pageindex_config,
        pipeline,
        index_params_hash,
        policy=cache_policy_for_config(cfg),
    )

    entries: list[SkillEntryRef] = []
    for ref in rust_refs:
        content_hash = str(ref["content_sha256"])
        if content_hash not in source_by_hash:
            continue
        entries.append(
            _entry_from_rust_ref(
                ref,
                source_by_hash[content_hash],
                pipeline=pipeline,
                index_params=index_params,
                index_params_hash=index_params_hash,
                pageindex_config=pageindex_config,
            ),
        )

    from cyt.hook.workspace_config import hook_workspace_from_config
    from cyt.permissions.runtime import filter_skill_entries, resolve_effective_permissions

    effective = resolve_effective_permissions(
        config=cfg,
        agent=active_agent,
    )
    workspace = hook_workspace_from_config(cfg)
    workspace_base = Path(str(workspace)).expanduser() if workspace else None
    return _filter_agent_system_skills(
        filter_skill_entries(entries, effective.skills.deny, base=workspace_base),
        active_agent=active_agent,
    )
