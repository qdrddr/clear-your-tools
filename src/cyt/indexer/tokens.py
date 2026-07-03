"""Token counting — re-export from cyt-indexer-sdk."""

from __future__ import annotations

import json
import logging
import sys

from cyt_indexer.tokens import (
    count_json_tokens,
    count_tokens,
    count_tokens_batch,
    truncate_description,
)

logger = logging.getLogger(__name__)

__all__ = [
    "compact_json",
    "count_json_tokens",
    "count_tokens",
    "count_tokens_batch",
    "log_token_usage",
    "truncate_description",
]


def compact_json(obj: object) -> str:
    """Serialize JSON without indentation (stable token accounting)."""
    try:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return "null"


def log_token_usage(label: str, tokens: int) -> None:
    """Log a token count line to stderr (stdout is reserved for hook JSON)."""
    msg = f"{label}: {tokens} tokens"
    logger.info(msg)
    print(msg, file=sys.stderr, flush=True)  # ast-grep-ignore: no-print-statements
