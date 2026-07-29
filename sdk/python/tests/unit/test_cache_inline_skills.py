"""Tests for inline client/hook skill sources via ensure_skills_registry."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cyt_indexer.cache import ensure_skills_registry
from cyt_indexer.pageindex import default_page_index_config


def test_ensure_skills_registry_accepts_inline_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        catalog_root = Path(tmp) / "catalog"
        catalog_root.mkdir()
        body = "---\nname: inline-demo\n---\n# Title\n\nInline body\n"
        refs = ensure_skills_registry(
            [
                {
                    "path": "/virtual/client/inline-demo.md",
                    "content": body,
                    "content_sha256": "abc123deadbeef",  # pragma: allowlist secret
                },
            ],
            str(catalog_root),
            default_page_index_config().to_dict(),
            "bm25",
            "test-hash",
            policy="force_memory",
        )
        assert len(refs) == 1
        assert refs[0]["doc_id"] == "inline-demo"
        assert refs[0]["source_path"] == "/virtual/client/inline-demo.md"
        assert refs[0]["content_sha256"] == "abc123deadbeef"  # pragma: allowlist secret
        assert refs[0].get("lazy_pending") is not True
