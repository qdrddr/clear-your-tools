"""Re-export JSON chunker (moved to nogo.embedder.custom_json_chunker)."""

from nogo.embedder.custom_json_chunker import extract_semantic_lines, json_chunker

__all__ = ["extract_semantic_lines", "json_chunker"]
