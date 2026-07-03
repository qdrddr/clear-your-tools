"""LLM token counting via tiktoken-rs in Rust core."""

from __future__ import annotations

from cyt_indexer._native import count_json_tokens as _count_json_tokens_native
from cyt_indexer._native import count_tokens as _count_tokens_native
from cyt_indexer._native import count_tokens_batch as _count_tokens_batch_native
from cyt_indexer._native import configure_tokenizer_defaults as _configure_tokenizer_defaults_native
from cyt_indexer.build import truncate_description

__all__ = [
    "configure_tokenizer_defaults",
    "count_json_tokens",
    "count_tokens",
    "count_tokens_batch",
    "truncate_description",
]


def count_tokens(text: str) -> int:
    """Return tiktoken count for text under the configured encoding."""
    return int(_count_tokens_native(text))


def count_tokens_batch(texts: list[str]) -> list[int]:
    """Return tiktoken counts for multiple strings in one native call."""
    if not texts:
        return []
    return [int(n) for n in _count_tokens_batch_native(texts)]


def count_json_tokens(obj: object) -> int:
    """Compact-serialize obj and return its token count."""
    return int(_count_json_tokens_native(obj))


def configure_tokenizer_defaults(
    *,
    encoding: str = "cl100k_base",
    allowed_special: str = "all",
) -> None:
    """Override SDK tokenizer defaults in native core."""
    _configure_tokenizer_defaults_native(
        encoding=encoding,
        allowed_special=allowed_special,
    )
