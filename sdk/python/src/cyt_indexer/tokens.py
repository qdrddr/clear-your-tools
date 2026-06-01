"""Token counting — Rust-backed with Python log_token_usage."""

from __future__ import annotations

import logging
from typing import Any

from cyt_indexer._native import compact_json as _compact_json
from cyt_indexer._native import count_json_tokens as _count_json_tokens
from cyt_indexer._native import count_tokens as _count_tokens

logger = logging.getLogger(__name__)


def compact_json(obj: Any) -> str:
    """Serialize JSON without indentation (stable token accounting)."""
    return _compact_json(obj)


def count_tokens(text: str) -> int:
    """Return tiktoken count for text under cl100k_base."""
    return _count_tokens(text)


def count_json_tokens(obj: Any) -> int:
    """Compact-serialize obj and return its token count."""
    return _count_json_tokens(obj)


def log_token_usage(label: str, tokens: int) -> None:
    """Print and log a token count line (console + logger)."""
    msg = f"{label}: {tokens} tokens"
    logger.info(msg)
    print(msg, flush=True)
