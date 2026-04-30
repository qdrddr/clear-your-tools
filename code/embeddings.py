"""Shared embedding logic supporting local/in-process (SentenceTransformer) and remote (litellm) models via API.

Reads defaults.embedding_model_type and defaults.embedding_model_nick from
config.yaml, then dispatches to the appropriate backend.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
from configs import (
    DEFAULT_LOCAL_MODEL_NAME,
    load_config,
    remote_embedding_api_key,
)


def _resolve_remote_model(config: dict[str, Any], nick: str) -> tuple[str, str | None]:
    """Return (litellm_model_name, api_key_env_var) for a given nick."""
    for entry in config.get("models", {}).get("embeddings", {}).get("remote", []):
        if entry.get("nick") == nick:
            provider = entry.get("provider", "openrouter")
            name = entry.get("name")
            if name:
                key_var = entry.get("KeyVarName")
                return f"{provider}/{name}", key_var
    raise ValueError(f"Unknown remote embedding model nick: {nick}")


class Embedder:
    """Unified embedder with an encode() API compatible with SentenceTransformer."""

    def __init__(self) -> None:
        self._config = load_config()
        defaults = self._config.get("defaults", {})
        self._model_type = str(defaults.get("embedding_model_type")).lower()
        self._model_nick = str(defaults.get("embedding_model_nick"))
        self._local_model_name = DEFAULT_LOCAL_MODEL_NAME
        self._encoder: Any | None = None
        self._remote_model: str | None = None
        self._remote_api_key_var: str | None = None

        if self._model_type == "remote":
            self._remote_model, self._remote_api_key_var = _resolve_remote_model(
                self._config, self._model_nick
            )
        else:
            for entry in self._config.get("models", {}).get("embeddings", {}).get("inprocess", []):
                if entry.get("nick") == self._model_nick:
                    self._local_model_name = entry.get("name", self._local_model_name)
                    break

    def _init_local(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        if self._model_type != "inprocess":
            raise RuntimeError(
                "_init_local() must only be called when embedding_model_type is 'inprocess'"
            )
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError("sentence-transformers is required for local embeddings") from exc
        self._encoder = SentenceTransformer(self._local_model_name)
        return self._encoder

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

        api_key = remote_embedding_api_key(self._remote_api_key_var)
        response = litellm.embedding(
            model=self._remote_model,
            input=sentences,
            api_key=api_key,
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
