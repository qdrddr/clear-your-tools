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
    """Log pruning token usage; stderr output only when proxy debug logging is active."""
    msg = f"{label}: {tokens} tokens"
    logger.debug(msg)
    from cyt.proxy.transport import append_debug_log_block, debug_endpoint_proxy_log_path

    if (log_path := debug_endpoint_proxy_log_path.get()) is not None:
        print(msg, file=sys.stderr, flush=True)  # ast-grep-ignore: no-print-statements
        append_debug_log_block(log_path, label="operator", content=msg)
