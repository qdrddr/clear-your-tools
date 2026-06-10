"""BM25 lexical cohesion chunker (standalone, no pageindex)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from cyt_indexer._native import bm25_cohesion_chunk as _bm25_cohesion_chunk


@dataclass
class Bm25CohesionConfig:
    window_mode: str = "sentence"
    threshold: float = 0.8
    merge_threshold: float = 0.7
    chunk_size: int = 2048
    token_counter: str = "approximate"
    similarity_window: int = 3
    next_unit_size: int = 1
    skip_window: int = 0
    min_units_per_chunk: int = 1
    min_characters_per_sentence: int = 24
    min_characters_per_word: int = 2
    delimiters: list[str] = field(default_factory=lambda: [". ", "! ", "? ", "\n"])
    include_delim: str = "prev"
    use_stopwords: bool = True
    filter_window: int = 5
    filter_polyorder: int = 3
    filter_tolerance: float = 0.2
    stem_language: str = "english"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_bm25_cohesion_config() -> Bm25CohesionConfig:
    return Bm25CohesionConfig()


def chunk(
    text: str,
    config: Bm25CohesionConfig | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Split text into lexical-cohesion chunks using BM25 tokenization."""
    cfg = _cohesion_config_dict(config)
    return _bm25_cohesion_chunk(text, cfg)


def _cohesion_config_dict(
    config: Bm25CohesionConfig | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, Bm25CohesionConfig):
        return config.to_dict()
    return config


# Public alias for app/SDK config builders.
cohesion_config_dict = _cohesion_config_dict
