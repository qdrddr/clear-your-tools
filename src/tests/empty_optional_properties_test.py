"""Tests for empty optional root properties mitigation."""

from __future__ import annotations

import json
import unittest
from typing import Any

from build_index import DECOMPOSED_PREFIX, CatalogIndex, build_catalog_index
from retrieve_catalog import retrieve_tools
import tool_policies
from tool_policies import (
    EMPTY_OPTIONAL_FALLBACK_K,
    direct_root_optional_chunks_for_tool,
    drop_recomposed_tools_with_empty_properties,
    filter_recompose_json_entries,
    mitigate_empty_optional_properties,
    root_chunk_properties_empty,
    tool_id_had_empty_original_root_properties,
    tool_id_has_empty_decomposed_root,
)


TOOL_ID = "mcp__test__empty_optional_tool"
ORIGINALLY_EMPTY_TOOL_ID = "mcp__test__originally_empty_tool"
CATALOG_PREFIX = "src/catalog"


def _make_tool_entry() -> dict[str, Any]:
    return {
        "id": TOOL_ID,
        "server": "test",
        "tool": TOOL_ID,
        "summary": "Test tool",
        "full_schema": {
            "id": TOOL_ID,
            "name": TOOL_ID,
            "description": "All optional props",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "alpha": {"type": "string", "description": "alpha"},
                    "beta": {"type": "string", "description": "beta"},
                    "gamma": {"type": "string", "description": "gamma"},
                    "delta": {"type": "string", "description": "delta"},
                    "epsilon": {"type": "string", "description": "epsilon"},
                },
            },
        },
    }


def _make_originally_empty_tool_entry() -> dict[str, Any]:
    return {
        "id": ORIGINALLY_EMPTY_TOOL_ID,
        "server": "test",
        "tool": ORIGINALLY_EMPTY_TOOL_ID,
        "summary": "Originally empty root",
        "full_schema": {
            "id": ORIGINALLY_EMPTY_TOOL_ID,
            "name": ORIGINALLY_EMPTY_TOOL_ID,
            "description": "No properties in original schema",
            "inputSchema": {"type": "object", "properties": {}},
        },
    }


def _build_index() -> CatalogIndex:
    return build_catalog_index([_make_tool_entry()], [])


def _build_originally_empty_index() -> CatalogIndex:
    return build_catalog_index([_make_originally_empty_tool_entry()], [])


def _catalog_dict_entry(
    rel_path: str,
    content: dict[str, Any],
    *,
    score: float = 1.0,
) -> dict[str, Any]:
    file_path = f"{CATALOG_PREFIX}/{rel_path}"
    return {
        "id": TOOL_ID,
        "name": TOOL_ID,
        "file_path": file_path,
        "score": score,
        "start_line": 1,
        "end_line": 10,
        "language": "json",
        "content": content,
    }


def _root_entry(index: CatalogIndex, score: float = 1.0) -> dict[str, Any]:
    raw = index.files[f"{DECOMPOSED_PREFIX}{TOOL_ID}.json"]
    return _catalog_dict_entry(f"{DECOMPOSED_PREFIX}{TOOL_ID}.json", json.loads(raw), score=score)


def _optional_entries(index: CatalogIndex, scores: dict[str, float]) -> list[dict[str, Any]]:
    entries = []
    for rel_path, score in scores.items():
        raw = index.files[rel_path]
        entries.append(
            _catalog_dict_entry(rel_path, json.loads(raw), score=score),
        )
    return entries


class TestEmptyOptionalDetection(unittest.TestCase):
    index: CatalogIndex

    def __init__(self, method_name: str = "runTest") -> None:
        super().__init__(methodName=method_name)
        self.index = _build_index()

    def test_empty_decomposed_root(self) -> None:
        self.assertTrue(tool_id_has_empty_decomposed_root(self.index, TOOL_ID))

    def test_root_chunk_properties_empty(self) -> None:
        root = _root_entry(self.index)
        self.assertTrue(root_chunk_properties_empty(root))


class TestMitigateEmptyOptionalProperties(unittest.TestCase):
    index: CatalogIndex
    root: dict[str, Any]
    optional: list[dict[str, Any]]
    post_rerank_scored: dict[str, list[dict[str, Any]]]

    def __init__(self, method_name: str = "runTest") -> None:
        super().__init__(methodName=method_name)
        self.index = _build_index()
        self.root = _root_entry(self.index, score=0.5)
        prop_scores = {
            f"{DECOMPOSED_PREFIX}{TOOL_ID}/alpha.json": 0.9,
            f"{DECOMPOSED_PREFIX}{TOOL_ID}/beta.json": 0.8,
            f"{DECOMPOSED_PREFIX}{TOOL_ID}/gamma.json": 0.7,
            f"{DECOMPOSED_PREFIX}{TOOL_ID}/delta.json": 0.6,
            f"{DECOMPOSED_PREFIX}{TOOL_ID}/epsilon.json": 0.0001,
        }
        self.optional = _optional_entries(self.index, prop_scores)
        self.post_rerank_scored = {"json": [self.root, *self.optional]}

    def test_rerank_adds_top_three_direct_root_optional(self) -> None:
        filtered = filter_recompose_json_entries([self.root])
        result = mitigate_empty_optional_properties(
            filtered,
            catalog_index=self.index,
            post_rerank_scored=self.post_rerank_scored,
            pipeline=["rerank"],
        )
        added = direct_root_optional_chunks_for_tool(result, TOOL_ID)
        self.assertEqual(len(added), EMPTY_OPTIONAL_FALLBACK_K)
        names = {
            rel.split("/")[-1].replace(".json", "")
            for item in added
            for rel in [item["file_path"].split(CATALOG_PREFIX + "/")[-1]]
        }
        self.assertEqual(names, {"alpha", "beta", "gamma"})

    def test_rerank_recompose_has_three_properties(self) -> None:
        filtered = filter_recompose_json_entries([self.root])
        mitigated = mitigate_empty_optional_properties(
            filtered,
            catalog_index=self.index,
            post_rerank_scored=self.post_rerank_scored,
            pipeline=["rerank"],
        )
        tools = retrieve_tools(
            {"json": mitigated},
            catalog=self.index,
            apply_decomposed_score_filter=False,
        )
        self.assertEqual(len(tools), 1)
        schema = tools[0].get("inputSchema") or {}
        self.assertEqual(set(schema.get("properties", {}).keys()), {"alpha", "beta", "gamma"})

    def test_llm_drops_tool_with_no_optional_survivors(self) -> None:
        filtered = filter_recompose_json_entries([self.root])
        result = mitigate_empty_optional_properties(
            filtered,
            catalog_index=self.index,
            post_rerank_scored=self.post_rerank_scored,
            pipeline=["rerank", "llm"],
        )
        self.assertEqual(result, [])

    def test_always_include_not_dropped_by_safety_net(self) -> None:
        old_system = tool_policies.system_tool_policy
        old_per = dict(tool_policies.PER_TOOL_POLICIES)
        try:
            tool_policies.PER_TOOL_POLICIES[TOOL_ID] = "always_include"
            tools = [
                {
                    "name": TOOL_ID,
                    "description": "x",
                    "inputSchema": {"type": "object", "properties": {}},
                },
            ]
            kept = drop_recomposed_tools_with_empty_properties(tools, self.index)
            self.assertEqual(len(kept), 1)
        finally:
            tool_policies.system_tool_policy = old_system
            tool_policies.PER_TOOL_POLICIES.clear()
            tool_policies.PER_TOOL_POLICIES.update(old_per)


class TestOriginallyEmptyRootException(unittest.TestCase):
    """Tools that already had properties: {} before decomposition are left unchanged."""

    index: CatalogIndex
    root: dict[str, Any]

    def __init__(self, method_name: str = "runTest") -> None:
        super().__init__(methodName=method_name)
        self.index = _build_originally_empty_index()
        assert tool_id_has_empty_decomposed_root(self.index, ORIGINALLY_EMPTY_TOOL_ID)
        assert tool_id_had_empty_original_root_properties(self.index, ORIGINALLY_EMPTY_TOOL_ID)
        raw = self.index.files[f"{DECOMPOSED_PREFIX}{ORIGINALLY_EMPTY_TOOL_ID}.json"]
        self.root = _catalog_dict_entry(
            f"{DECOMPOSED_PREFIX}{ORIGINALLY_EMPTY_TOOL_ID}.json",
            json.loads(raw),
            score=0.5,
        )

    def test_rerank_does_not_add_fallback_properties(self) -> None:
        filtered = filter_recompose_json_entries([self.root])
        result = mitigate_empty_optional_properties(
            filtered,
            catalog_index=self.index,
            post_rerank_scored={"json": [self.root]},
            pipeline=["rerank"],
        )
        self.assertEqual(result, filtered)
        self.assertEqual(direct_root_optional_chunks_for_tool(result, ORIGINALLY_EMPTY_TOOL_ID), [])

    def test_llm_keeps_tool_with_empty_properties(self) -> None:
        filtered = filter_recompose_json_entries([self.root])
        result = mitigate_empty_optional_properties(
            filtered,
            catalog_index=self.index,
            post_rerank_scored={"json": [self.root]},
            pipeline=["rerank", "llm"],
        )
        self.assertEqual(len(result), 1)
        self.assertTrue(root_chunk_properties_empty(result[0]))

    def test_safety_net_keeps_originally_empty_tool(self) -> None:
        tools = [
            {
                "name": ORIGINALLY_EMPTY_TOOL_ID,
                "description": "x",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]
        kept = drop_recomposed_tools_with_empty_properties(tools, self.index)
        self.assertEqual(len(kept), 1)


if __name__ == "__main__":
    unittest.main()
