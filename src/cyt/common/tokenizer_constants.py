"""App-owned SDK tokenizer overrides."""

from __future__ import annotations


def configure_sdk_tokenizer_defaults() -> None:
    """Push app tokenizer defaults into cyt-indexer (Rust core)."""
    from cyt_indexer.tokens import configure_tokenizer_defaults

    configure_tokenizer_defaults(encoding="cl100k_base", allowed_special="all")
