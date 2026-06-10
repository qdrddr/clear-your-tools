"""App-facing pageindex config bridge tests."""

from __future__ import annotations

from cyt.indexer.pageindex import (
    default_page_index_config,
    page_index_config_from_app,
    page_index_config_from_mapping,
)


def test_page_index_config_from_app_reads_nested_pageindex() -> None:
    cfg = page_index_config_from_app(
        {
            "pageindex": {
                "bm25_cohesion": {"chunk_size": 1024, "skip_window": 1},
            },
        },
    )
    assert cfg is not None
    assert cfg["bm25_cohesion"]["chunk_size"] == 1024
    assert cfg["bm25_cohesion"]["skip_window"] == 1
    assert cfg["bm25_cohesion"]["window_mode"] == "sentence"
    assert cfg["enable_bm25_chunking"] is True


def test_page_index_config_from_app_disable_bm25_chunking() -> None:
    cfg = page_index_config_from_app({"pageindex": {"enable_bm25_chunking": False}})
    assert cfg is not None
    assert cfg["enable_bm25_chunking"] is False


def test_page_index_config_from_app_missing_section() -> None:
    assert page_index_config_from_app({}) is None
    assert page_index_config_from_app(None) is None


def test_default_matches_sdk() -> None:
    sdk = default_page_index_config().to_dict()
    mapped = page_index_config_from_mapping({})
    assert mapped["if_add_node_id"] == sdk["if_add_node_id"]
    assert mapped["bm25_cohesion"]["chunk_size"] == sdk["bm25_cohesion"]["chunk_size"]
