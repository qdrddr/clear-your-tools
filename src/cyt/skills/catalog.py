"""Per-skill catalog cache under ~/.config/cyt/skills/entries/."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from cyt_indexer import SkillsBuilder, repair_skill_chunks

from cyt.config import (
    load_config,
    skills_catalog_dir,
    skills_directories,
    skills_pageindex_config,
    skills_pipeline,
)

logger = logging.getLogger(__name__)

_DECOMPOSED_PREFIX = "skills/decomposed"


@dataclass(frozen=True)
class SkillEntryRef:
    source_path: str
    doc_id: str
    content_sha256: str
    cache_key: str
    entry_dir: str
    document: dict[str, Any]


def doc_id_from_path(source_path: Path) -> str:
    name = source_path.name
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.replace("/", "__").lower()


def content_sha256_for_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_cache_key(content_hash: str, pipeline: str, index_params: dict[str, Any]) -> str:
    canonical = json.dumps(index_params, sort_keys=True, separators=(",", ":"))
    payload = f"{content_hash}\0{pipeline}\0{canonical}".encode()
    return hashlib.sha256(payload).hexdigest()


def _index_params(config: dict[str, Any] | None) -> dict[str, Any]:
    pageindex = skills_pageindex_config(config) or {}
    return dict(pageindex)


def _doc_dir(entry_dir: Path, doc_id: str) -> Path:
    return entry_dir / _DECOMPOSED_PREFIX / doc_id


def _iter_chunk_ids(structure: object) -> list[int]:
    chunk_ids: list[int] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        mapping = cast(dict[str, Any], node)
        chunks = mapping.get("chunks")
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, dict) and "chunk_id" in chunk:
                    chunk_ids.append(int(chunk["chunk_id"]))
        children = mapping.get("nodes")
        if isinstance(children, list):
            for child in children:
                walk(child)

    walk(structure)
    return chunk_ids


def _entry_state(entry_dir: Path, doc_id: str, document: dict[str, Any]) -> str:
    doc_json = _doc_dir(entry_dir, doc_id) / "document.json"
    if not doc_json.is_file():
        return "missing"
    structure = document.get("structure")
    if not structure:
        return "partial"
    for chunk_id in _iter_chunk_ids(structure):
        chunk_path = _doc_dir(entry_dir, doc_id) / "chunks" / f"{chunk_id}.md"
        if not chunk_path.is_file():
            return "partial"
    return "complete"


def _load_document_json(entry_dir: Path, doc_id: str) -> dict[str, Any]:
    doc_json = _doc_dir(entry_dir, doc_id) / "document.json"
    with doc_json.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"invalid document.json for {doc_id}")
    return data


def _augment_document_json(
    entry_dir: Path,
    doc_id: str,
    *,
    content_hash: str,
    pipeline: str,
    index_params: dict[str, Any],
    source_path: str,
) -> dict[str, Any]:
    doc_json_path = _doc_dir(entry_dir, doc_id) / "document.json"
    document = _load_document_json(entry_dir, doc_id)
    document["content_sha256"] = content_hash
    document["pipeline"] = pipeline
    document["index_params"] = index_params
    document["built_at"] = datetime.now(UTC).isoformat()
    if not document.get("path"):
        document["path"] = _shorten_home_path(source_path)
    with doc_json_path.open("w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    return document


def _shorten_home_path(path: str) -> str:
    expanded = Path(path).expanduser()
    home = Path.home()
    try:
        rel = expanded.relative_to(home)
        return f"~/{rel.as_posix()}"
    except ValueError:
        return expanded.as_posix()


def _full_build_entry(
    source_path: Path,
    entry_dir: Path,
    *,
    pipeline: str,
    index_params: dict[str, Any],
    pageindex_config: dict[str, Any] | None,
    content_hash: str,
) -> dict[str, Any]:
    entry_dir.mkdir(parents=True, exist_ok=True)
    doc_id = doc_id_from_path(source_path)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        dest = tmp_path / source_path.name
        shutil.copy2(source_path, dest)
        builder = SkillsBuilder(memory_only=False, output_dir=str(entry_dir))
        builder.build_from_dirs([str(tmp_path)], config=pageindex_config)
        builder.write_catalog()
    return _augment_document_json(
        entry_dir,
        doc_id,
        content_hash=content_hash,
        pipeline=pipeline,
        index_params=index_params,
        source_path=str(source_path),
    )


def _repair_entry(
    entry_dir: Path,
    doc_id: str,
    *,
    pageindex_config: dict[str, Any] | None,
) -> dict[str, Any]:
    repair_skill_chunks(str(entry_dir), doc_id, config=pageindex_config)
    return _load_document_json(entry_dir, doc_id)


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


def build_registry(config: dict[str, Any] | None = None) -> list[SkillEntryRef]:
    """Scan configured skill directories and return in-memory entry metadata."""
    cfg = config or load_config()
    expanded_dirs = skills_directories(cfg)

    catalog_root = Path(skills_catalog_dir(cfg))
    pipeline = skills_pipeline(cfg)
    index_params = _index_params(cfg)
    pageindex_config = skills_pageindex_config(cfg)

    seen_content: set[str] = set()
    entries: list[SkillEntryRef] = []

    for source_path in _walk_skill_md_files(expanded_dirs):
        content_hash = content_sha256_for_file(source_path)
        if content_hash in seen_content:
            continue
        seen_content.add(content_hash)

        cache_key = compute_cache_key(content_hash, pipeline, index_params)
        entry_dir = catalog_root / "entries" / cache_key
        doc_id = doc_id_from_path(source_path)

        document: dict[str, Any] | None = None
        if (_doc_dir(entry_dir, doc_id) / "document.json").is_file():
            document = _load_document_json(entry_dir, doc_id)
        state = _entry_state(entry_dir, doc_id, document or {})

        if state == "missing":
            document = _full_build_entry(
                source_path,
                entry_dir,
                pipeline=pipeline,
                index_params=index_params,
                pageindex_config=pageindex_config,
                content_hash=content_hash,
            )
        elif state == "partial":
            document = _repair_entry(
                entry_dir,
                doc_id,
                pageindex_config=pageindex_config,
            )
        elif document is None:
            continue

        entries.append(
            SkillEntryRef(
                source_path=str(source_path),
                doc_id=doc_id,
                content_sha256=content_hash,
                cache_key=cache_key,
                entry_dir=str(entry_dir),
                document=document,
            ),
        )

    return entries
