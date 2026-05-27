"""Re-export embedder API (moved to nogo.embedder.embeddings)."""

from nogo.embedder.embeddings import Embedder, get_embedder

__all__ = ["Embedder", "get_embedder"]
