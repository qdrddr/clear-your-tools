"""Shared embedding logic supporting local/in-process (SentenceTransformer) and remote (litellm) models via API.

Reads defaults.embedding_model_type and defaults.embedding_model_nick from
config.yaml, then dispatches to the appropriate backend.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np

_SRC_ROOT = Path(__file__).resolve().parent.parent.parent
_src_root_str = str(_SRC_ROOT)
if _src_root_str not in sys.path:
    sys.path.insert(0, _src_root_str)

from configs import resolve_model
from nogo.embedder.configs import load_embedder_config


class Embedder:
    """Unified embedder with an encode() API compatible with SentenceTransformer."""

    def __init__(self) -> None:
        self._config = load_embedder_config()
        defaults = self._config.get("defaults", {})
        self._model_type = str(defaults.get("embedding_model_type")).lower()
        self._model_nick = str(defaults.get("embedding_model_nick"))
        self._encoder: Any | None = None
        self._model_name: str | None = None
        self._provider_api_key_var: str | None = None
        self._provider_base_url: str | None = None
        self._dim: int | None = None

        self._model_name, self._provider_api_key_var, self._provider_base_url = resolve_model(
            self._model_nick,
            "embeddings",
            self._model_type,
            config=self._config,
        )

    def _init_local(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        if self._model_type != "inprocess":
            raise RuntimeError(
                "_init_local() must only be called when embedding_model_type is 'inprocess'",
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("sentence-transformers is required for local embeddings") from exc
        self._encoder = SentenceTransformer(self._model_name)
        return self._encoder

    @property
    def dim(self) -> int:
        if self._dim is not None:
            return self._dim
        if self._model_type == "inprocess":
            encoder = self._init_local()
            self._dim = encoder.get_sentence_embedding_dimension()
        else:
            try:
                import litellm
            except ImportError as exc:
                raise ImportError("litellm is required for remote embeddings") from exc
            response = litellm.embedding(
                model=self._model_name,
                input=["test"],
                api_key=self._provider_api_key_var,
                base_url=self._provider_base_url,
            )
            self._dim = len(response.data[0]["embedding"])
        return self._dim

    def encode(
        self,
        sentences: str | list[str],
        *,
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """Encode texts into a normalized float32 numpy array.

        Matches the SentenceTransformer.encode signature for the args used
        elsewhere in the codebase.
        """
        if isinstance(sentences, str):
            sentences = [sentences]
            was_single = True
        else:
            was_single = False

        if self._model_type == "inprocess":
            encoder = self._init_local()
            vectors = encoder.encode(
                sentences,
                normalize_embeddings=normalize_embeddings,
                show_progress_bar=show_progress_bar,
            )
            arr = np.asarray(vectors, dtype="float32")
            return arr[0] if was_single else arr

        # Remote path via litellm
        try:
            import litellm
        except ImportError as exc:
            raise ImportError("litellm is required for remote embeddings") from exc

        response = litellm.embedding(
            model=self._model_name,
            input=sentences,
            api_key=self._provider_api_key_var,
            base_url=self._provider_base_url,
        )
        embeddings = [item["embedding"] for item in response.data]
        arr = np.asarray(embeddings, dtype="float32")

        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms

        return arr[0] if was_single else arr


_embedder_singleton: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder_singleton
    if _embedder_singleton is None:
        _embedder_singleton = Embedder()
    return _embedder_singleton
