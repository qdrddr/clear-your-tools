"""ToolVectorStore: LanceDB-backed store of compact tool summaries.

Companion code for "Tool Attention Is All You Need"
(Anuj Sadani, 2026). Each summary must be a short natural-language
sentence (<= 60 tokens under cl100k_base) that reads as a user
intent (e.g., "Search GitHub issues by label and assignee").

Uses LanceDB for dense semantic embeddings and native BM25/FTS
full-text search. Hybrid search is performed via LanceDB's built-in
hybrid query builder which fuses vector and keyword results.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast, final

import lancedb
import litellm
import numpy as np
import pyarrow as pa
from configs import (
    _FINGERPRINT_KEYS,
    load_config,
    resolve_model,
)
from embeddings import get_embedder


class EmbeddingModelChangedError(Exception):
    """Raised when the active embedding model no longer matches the model used to populate the store."""

    def __init__(self, stored: dict[str, Any] | None, current: dict[str, Any]) -> None:
        self.stored = stored
        self.current = current
        super().__init__(
            f"Embedding model changed. stored={json.dumps(stored, sort_keys=True) if stored else 'None'}, "
            f"current={json.dumps(current, sort_keys=True)}"
        )


class _SparseVector(Protocol):
    """Structural stand-in for sparse vector representation."""

    indices: list[int]
    values: list[float]


@dataclass
class _SimpleSparseVector:
    """Simple sparse vector implementation for API compatibility."""

    indices: list[int]
    values: list[float]


@final
class ToolVectorStore:
    """LanceDB-backed store with dense semantic + native BM25/FTS hybrid search."""

    _META_FILE = "meta.json"

    def __init__(
        self,
        dim: int | None = None,
        collection_name: str = "tool_summaries",
        persist_dir: str | None = ".lancedb",
        preserve_old_collections: bool = False,
    ) -> None:
        if dim is None:
            raise ValueError("dim must be provided (use embedder.dim to infer it)")
        self.dim: int = dim
        self._base_collection_name: str = collection_name
        self._persist_dir: str | None = persist_dir
        self._preserve_old_collections: bool = preserve_old_collections
        self._current_fingerprint: dict[str, Any] = self._resolve_fingerprint(dim)
        if persist_dir is not None:
            self._db = lancedb.connect(persist_dir)
        else:
            self._temp_dir = tempfile.TemporaryDirectory()
            self._db = lancedb.connect(self._temp_dir.name)
        self.collection_name: str = ""
        self._maybe_cleanup_registry()
        self._schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dim)),
                pa.field("summary", pa.string()),
                pa.field("sparse_indices", pa.list_(pa.int32())),
                pa.field("sparse_values", pa.list_(pa.float32())),
            ]
        )
        try:
            self.table = self._db.open_table(self.collection_name)
        except Exception:
            self.table = self._db.create_table(self.collection_name, schema=self._schema)
        stored_fp = self._read_fingerprint()
        if stored_fp is None:
            self._write_fingerprint()
        elif stored_fp != self._current_fingerprint:
            raise EmbeddingModelChangedError(stored_fp, self._current_fingerprint)
        self.tool_ids: list[str] = []
        self.summaries: dict[str, str] = {}
        self._sparse: dict[str, _SparseVector] = {}

    @staticmethod
    def _resolve_fingerprint(dim: int) -> dict[str, Any]:
        config = load_config()
        defaults = config.get("defaults", {})
        model_nick = str(defaults.get("embedding_model_nick", ""))
        model_type = str(defaults.get("embedding_model_type", ""))
        if not model_nick or not model_type:
            return {
                "model_name": "",
                "model_type": "",
                "base_url": None,
                "dimensions": dim,
            }
        model_name, _, base_url = resolve_model(model_nick, "embeddings", model_type)
        return {
            "model_name": model_name,
            "model_type": model_type,
            "base_url": base_url,
            "dimensions": dim,
        }

    @staticmethod
    def _fingerprint_hash(fingerprint: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:10]

    @staticmethod
    def _current_fingerprint_name(base_name: str, fingerprint: dict[str, Any]) -> str:
        return f"{base_name}_{ToolVectorStore._fingerprint_hash(fingerprint)}"

    @staticmethod
    def _fingerprint_to_metadata(fingerprint: dict[str, Any]) -> dict[str, Any]:
        return {
            "hnsw:space": "cosine",
            "embedding_model_name": fingerprint["model_name"],
            "embedding_model_type": fingerprint["model_type"],
            "embedding_base_url": fingerprint["base_url"] or "",
            "embedding_dimensions": fingerprint["dimensions"],
        }

    @staticmethod
    def _metadata_to_fingerprint(metadata: dict[str, Any]) -> dict[str, Any] | None:
        if not all(k in metadata for k in _FINGERPRINT_KEYS):
            return None
        return {
            "model_name": metadata["embedding_model_name"],
            "model_type": metadata["embedding_model_type"],
            "base_url": metadata["embedding_base_url"] or None,
            "dimensions": metadata["embedding_dimensions"],
        }

    @property
    def _registry_path(self) -> Path | None:
        if self._persist_dir is None:
            return None
        return Path(self._persist_dir) / ".tool_vector_store_registry.json"

    def _read_registry(self) -> dict[str, Any] | None:
        path = self._registry_path
        if path is None or not path.exists():
            return None
        return cast(dict[str, Any], json.loads(path.read_text()))

    def _write_registry(self, registry: dict[str, Any] | None = None) -> None:
        path = self._registry_path
        if path is None:
            return
        if registry is None:
            registry = {
                "current_fingerprint_name": getattr(self, "collection_name", ""),
                "fingerprints": [],
            }
        path.write_text(json.dumps(registry, indent=2))

    def _maybe_cleanup_registry(self) -> None:
        registry = self._read_registry()
        if registry is None:
            registry = {"current_fingerprint_name": "", "fingerprints": []}

        current_fp = self._current_fingerprint
        fingerprints = cast(list[dict[str, Any]], registry.get("fingerprints", []))
        entry: dict[str, Any] | None = None
        for fp in fingerprints:
            if (
                fp.get("model_name") == current_fp["model_name"]
                and fp.get("model_type") == current_fp["model_type"]
                and fp.get("base_url") == current_fp["base_url"]
                and fp.get("dimensions") == current_fp["dimensions"]
            ):
                entry = fp
                break

        if entry:
            self.collection_name = entry["name"]
            registry["current_fingerprint_name"] = entry["name"]
        else:
            self.collection_name = self._current_fingerprint_name(
                self._base_collection_name, self._current_fingerprint
            )
            new_entry = {
                "model_name": current_fp["model_name"],
                "model_type": current_fp["model_type"],
                "base_url": current_fp["base_url"],
                "dimensions": current_fp["dimensions"],
                "name": self.collection_name,
            }
            fingerprints.append(new_entry)
            registry["current_fingerprint_name"] = self.collection_name

        self._write_registry(registry)

    @property
    def _fingerprint_path(self) -> Path | None:
        if self._persist_dir is None:
            return None
        return Path(self._persist_dir) / f".fingerprint_{self.collection_name}.json"

    def _read_fingerprint(self) -> dict[str, Any] | None:
        path = self._fingerprint_path
        if path is None or not path.exists():
            return None
        return cast(dict[str, Any], json.loads(path.read_text()))

    def _write_fingerprint(self) -> None:
        path = self._fingerprint_path
        if path is None:
            return
        path.write_text(json.dumps(self._current_fingerprint, indent=2))

    def _assert_fingerprint_match(self) -> None:
        stored = self._read_fingerprint()
        if stored is not None and stored != self._current_fingerprint:
            raise EmbeddingModelChangedError(stored, self._current_fingerprint)

    @staticmethod
    def _compute_sparse(summaries: list[str]) -> list[_SimpleSparseVector]:
        """Compute simple sparse term-frequency vectors for API compatibility."""
        vocab: dict[str, int] = {}
        docs_tokens: list[list[str]] = []
        for summary in summaries:
            tokens = re.findall(r"[a-z]+", summary.lower())
            docs_tokens.append(tokens)
            for token in set(tokens):
                if token not in vocab:
                    vocab[token] = len(vocab)

        sparse_list: list[_SimpleSparseVector] = []
        for tokens in docs_tokens:
            counts = Counter(tokens)
            pairs = sorted((vocab[token], float(count)) for token, count in counts.items())
            indices = [i for i, _ in pairs]
            values = [v for _, v in pairs]
            sparse_list.append(_SimpleSparseVector(indices, values))

        return sparse_list

    def _ensure_indexes(self) -> None:
        """Create vector and FTS indexes when enough data exists."""
        try:
            self.table.create_index(metric="cosine")
        except Exception:
            pass
        try:
            self.table.create_fts_index("summary")
        except Exception:
            pass

    def add_tools(
        self,
        tools: Sequence[Mapping[str, object]],
        encoder: Any | None = None,
    ) -> None:
        """Add tools to the index.

        `tools` must be a sequence of dicts with at least keys
        'id' (str) and 'summary' (str).
        """
        self._assert_fingerprint_match()
        if not tools:
            return
        embedder = encoder or get_embedder()
        summaries: list[str] = [cast(str, t["summary"]) for t in tools]
        ids: list[str] = [cast(str, t["id"]) for t in tools]
        vectors: np.ndarray = np.asarray(
            embedder.encode(summaries, normalize_embeddings=True, show_progress_bar=False)
        ).astype("float32")
        if vectors.shape[1] != self.dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dim}, got {vectors.shape[1]}"
            )
        sparse_embeddings = self._compute_sparse(summaries)

        records = []
        for tid, summary, vector, sp in zip(
            ids, summaries, vectors.tolist(), sparse_embeddings, strict=False
        ):
            records.append(
                {
                    "id": tid,
                    "vector": vector,
                    "summary": summary,
                    "sparse_indices": sp.indices,
                    "sparse_values": sp.values,
                }
            )

        self.table.add(records)
        for t, sp in zip(tools, sparse_embeddings, strict=False):
            self.tool_ids.append(cast(str, t["id"]))
            self.summaries[cast(str, t["id"])] = cast(str, t["summary"])
            self._sparse[cast(str, t["id"])] = sp

        self._ensure_indexes()

    def search(
        self,
        query_vec: np.ndarray,
        k: int,
        *,
        query_text: str | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to `k` (tool_id, score) pairs sorted by score desc.

        When *query_text* is supplied the method uses LanceDB's native hybrid
        search to fuse dense semantic rankings with BM25/FTS keyword rankings.
        Otherwise it falls back to dense-only search.
        """
        self._assert_fingerprint_match()
        if not self.tool_ids:
            return []
        n_candidates = min(max(k * 4, 20), len(self.tool_ids))

        if query_text:
            results = (
                self.table.search(query_type="hybrid")
                .vector(query_vec.tolist())
                .text(query_text)
                .limit(n_candidates)
                .to_arrow()
                .to_pylist()
            )
            if not results:
                return []
            max_score = max(float(r["_relevance_score"]) for r in results)
            if max_score == 0:
                max_score = 1.0
            candidates = [(r["id"], float(r["_relevance_score"]) / max_score) for r in results]
        else:
            results = (
                self.table.search(query_vec.tolist()).limit(n_candidates).to_arrow().to_pylist()
            )
            candidates = [(r["id"], max(0.0, 1.0 - float(r["_distance"]))) for r in results]

        if query_text and candidates:
            reranked = self._rerank(query_text, candidates)
            if reranked is not None:
                candidates = reranked

        return candidates[:k]

    def _rerank(
        self,
        query_text: str,
        candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]] | None:
        """Call an external reranker and return re-sorted candidates, or None."""
        try:
            config = load_config()
            defaults = config.get("defaults", {})
            if not defaults.get("reranking_enabled"):
                return None
            model_nick = defaults.get("reranking_model_nick")
            model_type = defaults.get("reranking_model_type")
            if not model_nick or not model_type:
                return None

            model_name, api_key, base_url = resolve_model(
                str(model_nick), "rerankers", str(model_type)
            )

            candidate_ids = [tid for tid, _ in candidates]
            candidate_docs = [self.summaries[tid] for tid in candidate_ids]

            response = litellm.rerank(
                model=model_name,
                query=query_text,
                documents=candidate_docs,
                api_key=api_key,
                base_url=base_url,
            )
            reranked_scores: dict[str, float] = {}
            for item in response.results:
                idx = item.get("index") if isinstance(item, dict) else getattr(item, "index", None)
                score = (
                    item.get("relevance_score")
                    if isinstance(item, dict)
                    else getattr(item, "relevance_score", None)
                )
                if idx is not None and score is not None:
                    reranked_scores[candidate_ids[int(idx)]] = float(score)

            if reranked_scores:
                return sorted(reranked_scores.items(), key=lambda x: -x[1])
        except Exception:
            pass
        return None

    @staticmethod
    def _sparse_dot(a: _SparseVector, b: _SparseVector) -> float:
        """Dot product of two sparse vector objects."""
        b_dict: dict[int, float] = dict(zip(b.indices, b.values, strict=False))
        return sum(
            val * b_dict.get(idx, 0.0) for idx, val in zip(a.indices, a.values, strict=False)
        )

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        sparse_data: dict[str, dict[str, list[int] | list[float]]] = {
            tid: {"indices": sp.indices, "values": sp.values} for tid, sp in self._sparse.items()
        }
        _ = (path / self._META_FILE).write_text(
            json.dumps(
                {
                    "tool_ids": self.tool_ids,
                    "summaries": self.summaries,
                    "sparse_embeddings": sparse_data,
                    "fingerprint": self._current_fingerprint,
                },
                indent=2,
            )
        )
        if self.table.count_rows() > 0:
            records = self.table.to_arrow().to_pylist()
            lancedb_data: dict[str, object] = {
                "records": [
                    {
                        "id": r["id"],
                        "vector": r["vector"],
                        "summary": r["summary"],
                        "sparse_indices": r["sparse_indices"],
                        "sparse_values": r["sparse_values"],
                    }
                    for r in records
                ],
            }
            _ = (path / "lancedb_data.json").write_text(json.dumps(lancedb_data, indent=2))

    @staticmethod
    def _require_dim(dim: int | None) -> int:
        if dim is None:
            raise ValueError("dim must be provided (use embedder.dim to infer it)")
        return dim

    @classmethod
    def load(
        cls,
        path: Path,
        dim: int | None = None,
        collection_name: str = "tool_summaries",
        persist_dir: str | None = ".lancedb",
        preserve_old_collections: bool = False,
    ) -> ToolVectorStore:
        dim = cls._require_dim(dim)
        cls._validate_load_path(path, cls._resolve_fingerprint(dim))

        store = cls(
            dim=dim,
            collection_name=collection_name,
            persist_dir=persist_dir,
            preserve_old_collections=preserve_old_collections,
        )
        cls._load_metadata(path, store)
        cls._load_lancedb_data(path, store)

        return store

    @classmethod
    def _validate_load_path(cls, path: Path, current_fp: dict[str, Any]) -> None:
        meta_path = path / cls._META_FILE
        if meta_path.exists():
            meta = cast(dict[str, object], json.loads(meta_path.read_text()))
            stored_fp = cast(dict[str, Any] | None, meta.get("fingerprint"))
            if stored_fp is not None and stored_fp != current_fp:
                raise EmbeddingModelChangedError(stored_fp, current_fp)

    @staticmethod
    def _load_metadata(path: Path, store: ToolVectorStore) -> None:
        meta_path = path / store._META_FILE
        if not meta_path.exists():
            return

        meta = cast(dict[str, object], json.loads(meta_path.read_text()))
        store.tool_ids = list(cast(list[str], meta["tool_ids"]))
        store.summaries = dict(cast(dict[str, str], meta["summaries"]))
        sparse_embeddings = cast(
            dict[str, dict[str, list[int] | list[float]]],
            meta.get("sparse_embeddings", {}),
        )
        for tid, sp_data in sparse_embeddings.items():
            store._sparse[tid] = _SimpleSparseVector(
                indices=cast(list[int], sp_data["indices"]),
                values=cast(list[float], sp_data["values"]),
            )

    @staticmethod
    def _load_lancedb_data(path: Path, store: ToolVectorStore) -> None:
        data_path = path / "lancedb_data.json"
        if not data_path.exists():
            return

        with open(data_path) as f:
            lancedb_data = cast(dict[str, object], json.load(f))

        records = cast(list[dict[str, object]], lancedb_data.get("records", []))
        if not records:
            return

        store.table.merge_insert(
            "id"
        ).when_matched_update_all().when_not_matched_insert_all().execute(records)

        for r in records:
            tid = cast(str, r["id"])
            sp_indices = r.get("sparse_indices")
            sp_values = r.get("sparse_values")
            if sp_indices is not None and sp_values is not None:
                if tid not in store._sparse:
                    store._sparse[tid] = _SimpleSparseVector(
                        indices=cast(list[int], sp_indices),
                        values=cast(list[float], sp_values),
                    )
                if tid not in store.tool_ids:
                    store.tool_ids.append(tid)

        store._ensure_indexes()

    @property
    def sparse_embeddings(self) -> dict[str, _SparseVector]:
        """Read-only access to the in-memory sparse embedding cache."""
        return self._sparse

    def __len__(self) -> int:
        return int(self.table.count_rows())
