"""Runtime score/policy defaults (values live in Rust; app pushes overrides via configure)."""

from __future__ import annotations

from cyt_indexer._native import (
    configure_runtime_defaults as _configure_runtime_defaults_native,
)
from cyt_indexer._native import (
    runtime_decomposed_score as _runtime_decomposed_score,
)
from cyt_indexer._native import (
    runtime_default_mcp_policy as _runtime_default_mcp_policy,
)
from cyt_indexer._native import (
    runtime_default_system_policy as _runtime_default_system_policy,
)
from cyt_indexer._native import (
    runtime_empty_optional_fallback_k as _runtime_empty_optional_fallback_k,
)
from cyt_indexer._native import (
    runtime_enum_score as _runtime_enum_score,
)
from cyt_indexer._native import (
    runtime_rerank_score as _runtime_rerank_score,
)


def configure_runtime_defaults(
    *,
    decomposed_score: float,
    enum_score: float,
    rerank_score: float,
    empty_optional_fallback_k: int,
    default_system_policy: str,
    default_mcp_policy: str,
) -> None:
    """Override SDK runtime defaults in native core."""
    _configure_runtime_defaults_native(
        decomposed_score,
        enum_score,
        rerank_score,
        empty_optional_fallback_k,
        default_system_policy,
        default_mcp_policy,
    )


def decomposed_score() -> float:
    return _runtime_decomposed_score()


def enum_score() -> float:
    return _runtime_enum_score()


def rerank_score() -> float:
    return _runtime_rerank_score()


def empty_optional_fallback_k() -> int:
    return _runtime_empty_optional_fallback_k()


def default_system_policy() -> str:
    return _runtime_default_system_policy()


def default_mcp_policy() -> str:
    return _runtime_default_mcp_policy()
