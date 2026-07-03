"""Tests for cyt.indexer.tokens (tiktoken cl100k_base)."""

from types import SimpleNamespace
from typing import cast

import pytest

from cyt.indexer.build import ToolSchemaSource, prepare_tool_entry, truncate_description
from cyt.indexer.tokens import (
    compact_json,
    count_json_tokens,
    count_tokens,
    count_tokens_batch,
    log_token_usage,
)
from cyt.pruners.rerank import count_rerank_request_tokens, rerank_bulk_base_tokens


def test_compact_json_preserves_unicode() -> None:
    obj = {"msg": "café"}
    serialized = compact_json(obj)
    assert "é" in serialized
    assert "\\u00e9" not in serialized


def test_compact_json_invalid_returns_null() -> None:
    assert compact_json(object()) == "null"


def test_count_tokens_is_positive() -> None:
    text = "hello world"
    assert count_tokens(text) >= 1
    assert count_tokens(text) == count_tokens(text)


def test_count_tokens_batch_matches_individual() -> None:
    texts = ["hello world", "café token count", ""]
    batch = count_tokens_batch(texts)
    assert batch == [count_tokens(text) for text in texts]


def test_count_json_tokens_matches_compact_serialization() -> None:
    obj = {"tools": [{"name": "x", "description": "café"}]}
    compact = compact_json(obj)
    assert count_json_tokens(obj) == count_tokens(compact)


def test_rerank_bulk_base_tokens_uses_json_payload() -> None:
    query = "find auth code"
    base = rerank_bulk_base_tokens(query)
    assert base == count_json_tokens({"query": query, "documents": []})
    assert base > count_tokens(query)


def test_count_rerank_request_tokens_empty_docs() -> None:
    query = "find auth code"
    assert count_rerank_request_tokens(query, []) == rerank_bulk_base_tokens(query)


def test_truncate_description_short_text_unchanged() -> None:
    text = "short tool description"
    assert truncate_description(text, max_tokens=60) == text


def test_truncate_description_respects_token_limit() -> None:
    text = "word " * 500
    result = truncate_description(text, max_tokens=60)
    assert result.endswith("...")
    assert count_tokens(result) <= 60


def test_truncate_description_word_boundary() -> None:
    words = ["intro"] + [f"word{i}" for i in range(200)]
    text = " ".join(words)
    result = truncate_description(text, max_tokens=25)
    if body := result.removesuffix("..."):
        assert body.split()[-1] in words


def test_truncate_description_reexported_from_build() -> None:
    from cyt_indexer import truncate_description as sdk_truncate

    assert truncate_description is sdk_truncate


def test_prepare_tool_entry_summary_within_token_budget() -> None:
    tool = SimpleNamespace(
        name="my_tool",
        description="x " * 400,
        inputSchema={"type": "object"},
    )
    entry = prepare_tool_entry("srv", cast(ToolSchemaSource, tool))
    assert count_tokens(entry["summary"]) <= 60


def test_count_rerank_request_tokens_with_docs() -> None:
    query = "q"
    docs = ["doc a", "doc b"]
    assert count_rerank_request_tokens(query, docs) == count_json_tokens(
        {"query": query, "documents": docs},
    )


def test_log_token_usage_writes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    log_token_usage("pruning model tokens (llm)", 42)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "pruning model tokens (llm): 42 tokens" in captured.err
