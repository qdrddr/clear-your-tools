"""App-owned SDK tokenizer overrides."""

from __future__ import annotations


def configure_sdk_tokenizer_defaults() -> None:
    """Push app tokenizer defaults into cyt-indexer (Rust core)."""
    from cyt_core.bootstrap import configure_sdk_tokenizer_defaults as _configure

    _configure()
