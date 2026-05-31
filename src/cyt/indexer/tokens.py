import json
import logging
from typing import Any

import tiktoken

logger = logging.getLogger(__name__)

_tiktoken_encoding_cache: tiktoken.Encoding | None = None


def _tiktoken_encoding() -> tiktoken.Encoding:
    global _tiktoken_encoding_cache
    if _tiktoken_encoding_cache is None:
        _tiktoken_encoding_cache = tiktoken.get_encoding("cl100k_base")
    return _tiktoken_encoding_cache


def compact_json(obj: Any) -> str:
    """Serialize JSON without indentation (stable token accounting)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def count_tokens(text: str) -> int:
    """Return tiktoken count for text under cl100k_base."""
    return len(_tiktoken_encoding().encode(text))


def count_json_tokens(obj: Any) -> int:
    """Compact-serialize obj and return its token count."""
    return count_tokens(compact_json(obj))


def log_token_usage(label: str, tokens: int) -> None:
    """Print and log a token count line (console + logger)."""
    msg = f"{label}: {tokens} tokens"
    logger.info(msg)
    print(msg, flush=True)
