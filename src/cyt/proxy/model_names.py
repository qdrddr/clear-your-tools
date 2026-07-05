"""Shared helpers for identifying real LLM model names in stats/config sync."""

from __future__ import annotations


def is_syncable_model_name(model_name: str | None) -> bool:
    """Return False for sentinel stats model names that are not real LLM models."""
    if not model_name:
        return False
    return model_name.strip().lower() != "hook"
