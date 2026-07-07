"""SDK bootstrap configuration types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bm25SdkConfig:
    """BM25 runtime settings for embeddable SDK bootstrap."""

    index_dir: str = ""
    stem_language: str = "english"
    stopwords: bool = True
    mmap: bool = False


__all__ = ["Bm25SdkConfig"]
