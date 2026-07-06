"""Tests for tools-hook stats recording."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cyt.proxy.stats import StatsDB
from cyt.tools.stats import record_tools_hook_injection


def test_record_tools_hook_injection_writes_signed_tokens() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "stats.db"
        config = {"stats": {"database": {"path": str(db_path)}}}
        record_tools_hook_injection(
            query="read files",
            model_name="claude-sonnet-4-20250514",
            tools_in=500,
            tools_out=120,
            prompt_tokens=25,
            config=config,
        )

        db = StatsDB.open(str(db_path))
        try:
            row = db._conn.execute(
                "SELECT endpoint, tools_in, tools_out, tools_pruned FROM proxy_request",
            ).fetchone()
            assert row[0] == "tools-hook"
            assert row[1] == 500
            assert row[2] == 120
            assert row[3] == 380

            tokens = db._conn.execute(
                """
                SELECT m.stage, t.type, t.tokens, t.is_saved
                FROM tokens t
                JOIN model_request m ON m.id = t.model_request_id
                ORDER BY m.stage, t.type
                """,
            ).fetchall()
            upstream_saved = [
                (stage, typ, tok, saved)
                for stage, typ, tok, saved in tokens
                if stage == "upstream" and saved == 1
            ]
            assert ("upstream", "input", 380, 1) in upstream_saved
            assert ("upstream", "input", -25, 1) in upstream_saved
        finally:
            db.close()


def test_record_tools_hook_injection_records_effective_pipeline() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "stats.db"
        config = {"stats": {"database": {"path": str(db_path)}}}
        from cyt.common.token_usage import StageTokenUsage
        from cyt.pruners.bm25 import bm25_stage_usage

        record_tools_hook_injection(
            query="review auth flow",
            model_name="composer-2.5-fast",
            tools_in=900,
            tools_out=200,
            prompt_tokens=50,
            pruning_stages={
                "llm": StageTokenUsage(model_name="mercury-2"),
                "bm25": bm25_stage_usage(),
            },
            prune_status="applied",
            config=config,
        )

        db = StatsDB.open(str(db_path))
        try:
            row = db._conn.execute(
                "SELECT prune_status, pipeline FROM proxy_request",
            ).fetchone()
            assert row[0] == "applied"
            assert json.loads(row[1]) == ["bm25", "llm"]
        finally:
            db.close()
