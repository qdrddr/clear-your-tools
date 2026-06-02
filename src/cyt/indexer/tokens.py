"""Token counting — tiktoken cl100k_base."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

__all__ = [
    "compact_json",
    "count_json_tokens",
    "count_tokens",
    "log_token_usage",
]


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def compact_json(obj: Any) -> str:
    """Serialize JSON without indentation (stable token accounting)."""
    try:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return "null"


def count_tokens(text: str) -> int:
    """Return tiktoken count for text under cl100k_base."""
    return len(_encoding().encode(text, allowed_special="all"))


def count_json_tokens(obj: Any) -> int:
    """Compact-serialize obj and return its token count."""
    return count_tokens(compact_json(obj))


def log_token_usage(label: str, tokens: int) -> None:
    """Print and log a token count line (console + logger)."""
    msg = f"{label}: {tokens} tokens"
    logger.info(msg)
    print(msg, flush=True)  # ast-grep-ignore: no-print-statements
