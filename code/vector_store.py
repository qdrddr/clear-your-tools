"""ToolVectorStore: Chroma-backed store of compact tool summaries.

Companion code for "Tool Attention Is All You Need"
(Anuj Sadani, 2026). Each summary must be a short natural-language
sentence (<= 60 tokens under cl100k_base) that reads as a user
intent (e.g., "Search GitHub issues by label and assignee").

Uses ChromaDB for dense semantic embeddings and ChromaBm25EmbeddingFunction
for sparse keyword embeddings.  Hybrid search is performed via Reciprocal
Rank Fusion (RRF) of the dense and sparse rankings.

Note: Local (single-node) ChromaDB does not yet expose the ``Search`` /
``Knn`` / ``Rrf`` query API, so RRF is implemented manually in Python
for broad compatibility.  When running against Chroma Cloud or a
future local release that supports ``collection.search(Search(...))``,
the dense and sparse ``Knn`` expressions can be replaced with the native
Chroma RRF plan shown in the doc comments below.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast, final

import chromadb
import litellm
import numpy as np
from chromadb.api import ClientAPI
from chromadb.api.types import Metadatas, PyEmbeddings
from chromadb.utils.embedding_functions import ChromaBm25EmbeddingFunction
from configs import load_config, resolve_model
from embeddings import get_embedder

_FINGERPRINT_KEYS = (
    "embedding_model_name",
    "embedding_model_type",
    "embedding_base_url",
    "embedding_dimensions",
)


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
    """Structural stand-in for chromadb.base_types.SparseVector."""

    indices: list[int]
    values: list[float]


@final
class ToolVectorStore:
    """Chroma-backed store with dense semantic + BM25 sparse hybrid search."""

    def __init__(
        self,
        dim: int = 384,
        collection_name: str = "tool_summaries",
        persist_dir: str | None = ".chroma_db",
        preserve_old_collections: bool = False,
    ) -> None:
        self.dim: int = dim
        self._base_collection_name: str = collection_name
        self._persist_dir: str | None = persist_dir
        self._preserve_old_collections: bool = preserve_old_collections
        self._current_fingerprint: dict[str, Any] = self._resolve_fingerprint(dim)
        self.collection_name: str = self._effective_collection_name(
            collection_name, self._current_fingerprint
        )
        self.client: ClientAPI = (
            chromadb.PersistentClient(path=persist_dir)
            if persist_dir is not None
            else chromadb.Client()
        )
        self._maybe_cleanup_registry()
        metadata = self._fingerprint_to_metadata(self._current_fingerprint)
        self.collection: chromadb.Collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata=metadata,
        )
        stored_fp = self._metadata_to_fingerprint(self.collection.metadata or {})
        if stored_fp is None:
            self.collection.modify(metadata=metadata)
        elif stored_fp != self._current_fingerprint:
            raise EmbeddingModelChangedError(stored_fp, self._current_fingerprint)
        self.tool_ids: list[str] = []
        self.summaries: dict[str, str] = {}
        self._sparse: dict[str, _SparseVector] = {}
        self._bm25: ChromaBm25EmbeddingFunction = ChromaBm25EmbeddingFunction()

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
    def _effective_collection_name(base_name: str, fingerprint: dict[str, Any]) -> str:
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

    def _write_registry(self) -> None:
        path = self._registry_path
        if path is None:
            return
        path.write_text(
            json.dumps(
                {
                    "last_collection_name": self.collection_name,
                    "last_fingerprint": self._current_fingerprint,
                }
            )
        )

    def _maybe_cleanup_registry(self) -> None:
        registry = self._read_registry()
        if registry is None:
            self._write_registry()
            return
        old_name = registry.get("last_collection_name", "")
        old_fp = registry.get("last_fingerprint", {})
        if old_name and old_name != self.collection_name and old_fp != self._current_fingerprint:
            if not self._preserve_old_collections:
                try:
                    self.client.delete_collection(old_name)
                except Exception:
                    pass
        self._write_registry()

    def _assert_fingerprint_match(self) -> None:
        stored = self._metadata_to_fingerprint(self.collection.metadata or {})
        if stored is not None and stored != self._current_fingerprint:
            raise EmbeddingModelChangedError(stored, self._current_fingerprint)

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
            embedder.encode(  # pyright: ignore[reportUnknownMemberType]
                summaries, normalize_embeddings=True, show_progress_bar=False
            )
        ).astype("float32")
        if vectors.shape[1] != self.dim:
            raise ValueError(
                f"Embedding dimension mismatch: expected {self.dim}, got {vectors.shape[1]}"
            )
        sparse_embeddings: list[_SparseVector] = cast(list[_SparseVector], self._bm25(summaries))

        # Persist sparse vectors as ChromaDB metadata so they live in the
        # collection (not just an in-memory sidecar).  Native sparse-vector
        # indexing is unavailable in local/embedded ChromaDB, so we still
        # compute keyword scores manually in search().
        metadatas: list[dict[str, object]] = [
            {"sparse_indices": sp.indices, "sparse_values": sp.values} for sp in sparse_embeddings
        ]

        self.collection.add(
            ids=ids,
            embeddings=cast(PyEmbeddings, vectors.tolist()),
            documents=summaries,
            metadatas=cast(Metadatas, metadatas),
        )
        for t, sp in zip(tools, sparse_embeddings, strict=False):
            self.tool_ids.append(cast(str, t["id"]))
            self.summaries[cast(str, t["id"])] = cast(str, t["summary"])
            self._sparse[cast(str, t["id"])] = sp

    def search(
        self,
        query_vec: np.ndarray,
        k: int,
        *,
        query_text: str | None = None,
    ) -> list[tuple[str, float]]:
        """Return up to `k` (tool_id, score) pairs sorted by score desc.

        When *query_text* is supplied the method fuses dense semantic
        rankings with BM25 sparse keyword rankings via RRF (k=60,
        equal weights).  Otherwise it falls back to dense-only search.
        """
        self._assert_fingerprint_match()
        if not self.tool_ids:
            return []
        n_candidates = min(max(k * 4, 20), len(self.tool_ids))

        # Dense semantic search via ChromaDB
        dense_results = self.collection.query(
            query_embeddings=[query_vec.tolist()],
            n_results=n_candidates,
            include=["distances"],
        )
        dense_ids = dense_results["ids"][0]
        distances = dense_results["distances"]
        if distances is None:
            return []
        dense_distances = distances[0]
        dense_rank = {tid: rank for rank, tid in enumerate(dense_ids)}

        candidates: list[tuple[str, float]]

        # Hybrid: RRF with BM25 sparse keyword search
        if query_text and self._sparse:
            query_sparse = self._bm25([query_text])[0]
            sparse_scores = {
                tid: self._sparse_dot(query_sparse, sp) for tid, sp in self._sparse.items()
            }
            sorted_sparse = sorted(sparse_scores.items(), key=lambda x: -x[1])
            sparse_rank = {tid: rank for rank, (tid, _) in enumerate(sorted_sparse)}

            all_ids = set(dense_rank.keys()) | set(sparse_rank.keys())
            rrf_scores: dict[str, float] = {}
            for tid in all_ids:
                score = 0.0
                if tid in dense_rank:
                    score += 1.0 / (60 + dense_rank[tid])
                if tid in sparse_rank:
                    score += 1.0 / (60 + sparse_rank[tid])
                rrf_scores[tid] = score

            # Normalise to roughly the same scale as cosine similarity [0, 1]
            max_rrf = 2.0 / 60  # two rankings, both at rank 0
            ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])
            candidates = [(tid, float(score) / max_rrf) for tid, score in ranked[:n_candidates]]
        else:
            # Fallback to dense-only
            candidates = [
                (tid, 1.0 / (1.0 + dist))
                for tid, dist in zip(dense_ids, dense_distances, strict=False)
            ][:n_candidates]

        # Optional reranking stage
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
        """Dot product of two ChromaDB SparseVector objects."""
        b_dict: dict[int, float] = dict(zip(b.indices, b.values, strict=False))
        return sum(
            val * b_dict.get(idx, 0.0) for idx, val in zip(a.indices, a.values, strict=False)
        )

    def save(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        sparse_data: dict[str, dict[str, list[int] | list[float]]] = {
            tid: {"indices": sp.indices, "values": sp.values} for tid, sp in self._sparse.items()
        }
        _ = (path / "meta.json").write_text(
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
        if self.collection.count() > 0:
            result = self.collection.get(include=["embeddings", "documents", "metadatas"])
            embeddings = result.get("embeddings")
            chroma_data: dict[str, object] = {
                "ids": result["ids"],
                "embeddings": [
                    e.tolist() if isinstance(e, np.ndarray) else e
                    for e in (embeddings if embeddings is not None else [])
                ],
                "documents": result.get("documents") or [],
                "metadatas": result.get("metadatas") or [],
            }
            _ = (path / "chroma_data.json").write_text(json.dumps(chroma_data, indent=2))

    @classmethod
    def load(
        cls,
        path: Path,
        dim: int = 384,
        collection_name: str = "tool_summaries",
        persist_dir: str | None = ".chroma_db",
        preserve_old_collections: bool = False,
    ) -> ToolVectorStore:
        from chromadb.base_types import SparseVector

        current_fp = cls._resolve_fingerprint(dim)
        if (path / "meta.json").exists():
            meta = cast(
                dict[str, object],
                json.loads((path / "meta.json").read_text()),
            )
            stored_fp = cast(dict[str, Any] | None, meta.get("fingerprint"))
            if stored_fp is not None and stored_fp != current_fp:
                raise EmbeddingModelChangedError(stored_fp, current_fp)

        store = cls(
            dim=dim,
            collection_name=collection_name,
            persist_dir=persist_dir,
            preserve_old_collections=preserve_old_collections,
        )
        if (path / "meta.json").exists():
            meta = cast(
                dict[str, object],
                json.loads((path / "meta.json").read_text()),
            )
            store.tool_ids = list(cast(list[str], meta["tool_ids"]))
            store.summaries = dict(cast(dict[str, str], meta["summaries"]))
            sparse_embeddings = cast(
                dict[str, dict[str, list[int] | list[float]]],
                meta.get("sparse_embeddings", {}),
            )
            for tid, sp_data in sparse_embeddings.items():
                store._sparse[tid] = cast(
                    _SparseVector,
                    SparseVector(
                        indices=cast(list[int], sp_data["indices"]),
                        values=cast(list[float], sp_data["values"]),
                    ),
                )

        if (path / "chroma_data.json").exists():
            with open(path / "chroma_data.json") as f:
                chroma_data = cast(dict[str, object], json.load(f))
            ids = cast(list[str], chroma_data["ids"])
            embeddings = cast(list[list[float]], chroma_data["embeddings"])
            documents = cast(list[str], chroma_data["documents"])
            metadatas = cast(list[dict[str, object]], chroma_data["metadatas"])
            store.collection.upsert(
                ids=ids,
                embeddings=cast(list[Sequence[float]], embeddings),
                documents=documents,
                metadatas=cast(Metadatas, metadatas),
            )
            # Rebuild in-memory sparse cache from ChromaDB metadata
            # (covers cases where meta.json is missing / stale).
            for tid, meta in zip(ids, metadatas, strict=False):
                if tid not in store._sparse and meta:
                    sp_indices = meta.get("sparse_indices")
                    sp_values = meta.get("sparse_values")
                    if sp_indices is not None and sp_values is not None:
                        store._sparse[tid] = SparseVector(
                            indices=cast(list[int], sp_indices),
                            values=cast(list[float], sp_values),
                        )
                        if tid not in store.tool_ids:
                            store.tool_ids.append(tid)

        return store

    @property
    def sparse_embeddings(self) -> dict[str, _SparseVector]:
        """Read-only access to the in-memory sparse embedding cache."""
        return self._sparse

    def __len__(self) -> int:
        return self.collection.count()
