"""Tests for cyt_client transcript enrichment."""

from __future__ import annotations

import json
from pathlib import Path

from cyt_client.transcript import enrich_hook_payload


def test_enrich_skips_transcript_when_inline_prompt_present(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"role":"user","text":"old"}\n', encoding="utf-8")
    payload = {
        "prompt": "Use inline prompt for pruning",
        "transcript_path": str(transcript),
    }
    enriched = json.loads(
        enrich_hook_payload(json.dumps(payload).encode()),
    )
    assert "cyt_transcript" not in enriched


def test_enrich_loads_transcript_when_prompt_missing(tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"role":"user","text":"from file"}\n', encoding="utf-8")
    payload = {"transcript_path": str(transcript)}
    enriched = json.loads(
        enrich_hook_payload(json.dumps(payload).encode()),
    )
    assert enriched.get("cyt_transcript")
