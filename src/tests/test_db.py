"""Tests for embedded libSQL stats persistence."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Generator
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from cyt.common.token_usage import StageTokenUsage
from cyt.proxy.stats import ProxyRequestRecord, StatsDB, format_totals


@pytest.fixture
def temp_db() -> Generator[StatsDB]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        db = StatsDB.init(db_path)
        yield db
        db.close()


def _ts_ms(*, days_ago: int = 1, hour: int = 12) -> int:
    dt = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0) - timedelta(
        days=days_ago,
    )
    return int(dt.timestamp() * 1000)


def _set_proxy_ts(db: StatsDB, proxy_id: str, ts_ms: int) -> None:
    db._conn.execute("UPDATE proxy_request SET ts_ms = ? WHERE id = ?", (ts_ms, proxy_id))
    db._conn.commit()


def _sample_record(
    *,
    endpoint: str = "openai",
    tools_in: int = 100,
    tool_count_in: int = 2,
    tool_properties_count_in: int = 5,
    tools_out: int = 80,
    tool_count_out: int = 2,
    tool_properties_count_out: int = 3,
    prune_status: str = "applied",
    pipeline: list[str] | None = None,
    upstream_model_name: str | None = "gpt-5.4-mini",
    upstream_provider_dns: str | None = "api.openai.com",
    upstream_provider: str | None = "openai",
    pruning_stages: dict[str, StageTokenUsage] | None = None,
    error: str | None = None,
) -> ProxyRequestRecord:
    return ProxyRequestRecord(
        endpoint=endpoint,
        tools_in=tools_in,
        tool_count_in=tool_count_in,
        tool_properties_count_in=tool_properties_count_in,
        tools_out=tools_out,
        tool_count_out=tool_count_out,
        tool_properties_count_out=tool_properties_count_out,
        prune_status=prune_status,
        pipeline=["llm"] if pipeline is None else pipeline,
        upstream_model_name=upstream_model_name,
        upstream_provider_dns=upstream_provider_dns,
        upstream_provider=upstream_provider,
        pruning_stages={
            "llm": StageTokenUsage(
                input_tokens=10,
                output_tokens=5,
                usage_source="tiktoken:cl100k_base",
                model_name="openrouter/openai/gpt-oss-120b",
                provider="openrouter",
                provider_dns_name="openrouter.ai",
            ),
        }
        if pruning_stages is None
        else pruning_stages,
        error=error,
    )


def test_schema_init_and_record(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=1000,
        tool_count_in=10,
        tool_properties_count_in=50,
        tools_out=400,
        tool_count_out=8,
        tool_properties_count_out=20,
        prune_status="applied",
        pipeline=["llm"],
        upstream_model_name="google/gemini-3-flash-preview",
        upstream_provider_dns="openrouter.ai",
        upstream_provider="openrouter",
        pruning_stages={
            "llm": StageTokenUsage(
                input_tokens=500,
                output_tokens=50,
                usage_source="tiktoken:cl100k_base",
                model_name="openrouter/openai/gpt-oss-120b",
                provider="openrouter",
                provider_dns_name="openrouter.ai",
            ),
        },
    )
    proxy_id = temp_db.record_proxy_request(record)
    assert proxy_id

    totals = temp_db.query_totals()
    assert totals["events"] == 1
    assert totals["tools_accepted"] == 1000
    assert totals["tools_sent_upstream"] == 400
    assert totals["tools_saved"] == 600
    assert totals["llm_input"] == 500
    assert totals["llm_output"] == 50


def test_record_without_full_tools_json(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=100,
        tool_count_in=2,
        tool_properties_count_in=5,
        tools_out=80,
        tool_count_out=2,
        tool_properties_count_out=3,
        prune_status="pass_through",
        pipeline=[],
        tools_accepted_json=None,
        tools_final_json=None,
    )
    temp_db.record_proxy_request(record)
    events = temp_db.query_events(limit=1)
    assert events[0]["tools_pruned"] == 20


def test_query_totals_format() -> None:
    from cyt.common.pricing import StatsCosts

    costs = StatsCosts(
        tools_saved_usd=0.0005,
        llm_input_usd=0.000015,
        llm_output_usd=0.000012,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
    )
    text = format_totals(
        {
            "events": 3,
            "tools_accepted": 3000,
            "tools_sent_upstream": 1000,
            "tools_saved": 2000,
            "llm_input": 100,
            "llm_output": 20,
            "rerank_input": 0,
            "rerank_output": 0,
        },
        costs,
    )
    assert "tools saved:         2000  (66.7%)" in text
    assert "net savings (input tokens):" in text
    assert "  cost:         $0.000473" in text
    assert "  tokens:     1892 (63.1%)" in text


def test_query_totals_format_green_net_savings_tokens() -> None:
    from cyt.common.pricing import StatsCosts

    costs = StatsCosts(
        tools_saved_usd=0.0005,
        llm_input_usd=0.000015,
        llm_output_usd=0.000012,
        rerank_input_usd=0.0,
        rerank_output_usd=0.0,
    )
    totals = {
        "events": 3,
        "tools_accepted": 3000,
        "tools_sent_upstream": 1000,
        "tools_saved": 2000,
        "llm_input": 100,
        "llm_output": 20,
        "rerank_input": 0,
        "rerank_output": 0,
    }
    plain = format_totals(totals, costs, color=False)
    colored = format_totals(totals, costs, color=True)
    assert "  tokens:     1892 (63.1%)" in plain
    assert "\033[32m1892 (63.1%)\033[0m" in colored
    assert "  tokens:     \033[32m1892 (63.1%)\033[0m" in colored


def test_query_upstream_saved_tokens_and_costs(temp_db: StatsDB) -> None:
    from cyt.common.pricing import compute_stats_costs

    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=1000,
        tool_count_in=10,
        tool_properties_count_in=50,
        tools_out=400,
        tool_count_out=8,
        tool_properties_count_out=20,
        prune_status="applied",
        pipeline=["llm"],
        upstream_model_name="google/gemini-3-flash-preview",
        upstream_provider_dns="openrouter.ai",
        upstream_provider="openrouter",
    )
    temp_db.record_proxy_request(record)

    saved = temp_db.query_upstream_saved_tokens()
    assert saved == [("google/gemini-3-flash-preview", "openrouter.ai", 600)]

    config = {
        "models": {
            "llm": {
                "remote": [
                    {
                        "name": "google/gemini-3-flash-preview",
                        "nick": "gemini-3-flash",
                        "domain_match": ["openrouter.ai"],
                        "pricing": {"input_cost_per_token": 2.5e-07},
                    },
                ],
            },
        },
    }
    costs = compute_stats_costs([], saved, config)
    assert costs.tools_saved_usd == 600 * 2.5e-07


def test_record_bm25_pruning_stage_identity(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=100,
        tool_count_in=5,
        tool_properties_count_in=10,
        tools_out=80,
        tool_count_out=4,
        tool_properties_count_out=6,
        prune_status="applied",
        pipeline=["bm25"],
        pruning_stages={
            "bm25": StageTokenUsage(
                model_name="bm25",
                provider_dns_name="bm25",
                provider="bm25",
                usage_source="local:bm25",
            ),
        },
    )
    temp_db.record_proxy_request(record)

    identities = temp_db.query_distinct_model_identities()
    assert ("bm25", "bm25", "bm25", "bm25") in identities


def test_record_proxy_request_allows_null_provider(temp_db: StatsDB) -> None:
    record = ProxyRequestRecord(
        endpoint="anthropic",
        tools_in=100,
        tool_count_in=1,
        tool_properties_count_in=1,
        tools_out=50,
        tool_count_out=1,
        tool_properties_count_out=1,
        prune_status="applied",
        pipeline=["bm25"],
        upstream_model_name="unknown-model",
        upstream_provider_dns="api.unknown.example",
        upstream_provider=None,
    )
    proxy_id = temp_db.record_proxy_request(record)
    assert proxy_id

    rows = temp_db._conn.execute(
        "SELECT provider FROM model_request WHERE proxy_request_id = ? AND stage = 'upstream'",
        (proxy_id,),
    ).fetchall()
    assert rows == [(None,)]


def test_stats_db_init_creates_parent_dir() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "nested" / "stats.db")
        db = StatsDB.init(db_path)
        try:
            assert Path(db_path).exists()
        finally:
            db.close()


def test_has_error_set_on_insert(temp_db: StatsDB) -> None:
    proxy_id = temp_db.record_proxy_request(
        _sample_record(error="pruned catalog produced no tools"),
    )
    row = temp_db._conn.execute(
        "SELECT has_error, error FROM proxy_request WHERE id = ?",
        (proxy_id,),
    ).fetchone()
    assert row == (1, "pruned catalog produced no tools")

    clean_id = temp_db.record_proxy_request(_sample_record())
    clean = temp_db._conn.execute(
        "SELECT has_error FROM proxy_request WHERE id = ?",
        (clean_id,),
    ).fetchone()
    assert clean == (0,)


def test_has_error_migration_backfill() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "legacy.db")
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE proxy_request (
                id TEXT PRIMARY KEY,
                endpoint TEXT NOT NULL,
                tools_in INTEGER NOT NULL,
                tool_count_in INTEGER NOT NULL,
                tool_properties_count_in INTEGER NOT NULL,
                tools_out INTEGER NOT NULL,
                tool_count_out INTEGER NOT NULL,
                tool_properties_count_out INTEGER NOT NULL,
                tools_pruned INTEGER NOT NULL,
                tool_count_pruned INTEGER NOT NULL,
                tool_properties_count_pruned INTEGER NOT NULL,
                ts_ms INTEGER NOT NULL,
                prune_status TEXT NOT NULL,
                pipeline TEXT NOT NULL,
                query TEXT,
                error TEXT,
                tools_accepted_json TEXT,
                tools_final_json TEXT
            );
            """,
        )
        conn.execute(
            """
            INSERT INTO proxy_request (
                id, endpoint, tools_in, tool_count_in, tool_properties_count_in,
                tools_out, tool_count_out, tool_properties_count_out,
                tools_pruned, tool_count_pruned, tool_properties_count_pruned,
                ts_ms, prune_status, pipeline, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-1",
                "openai",
                1,
                1,
                1,
                1,
                1,
                1,
                0,
                0,
                0,
                1,
                "applied",
                '["llm"]',
                "boom",
            ),
        )
        conn.commit()
        conn.close()

        db = StatsDB.open(db_path)
        try:
            row = db._conn.execute(
                "SELECT has_error FROM proxy_request WHERE id = ?",
                ("legacy-1",),
            ).fetchone()
            assert row == (1,)
        finally:
            db.close()


def test_rollup_merges_same_day_same_keys(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=2)
    for _ in range(3):
        proxy_id = temp_db.record_proxy_request(_sample_record(tools_in=100, tools_out=80))
        _set_proxy_ts(temp_db, proxy_id, ts)

    totals_before = temp_db.query_totals()
    result = temp_db.rollup_historical(today=date.today())
    assert result.groups_merged == 1
    assert result.rows_removed == 2
    totals_after = temp_db.query_totals()
    for key, value in totals_before.items():
        if key == "events":
            continue
        assert totals_after[key] == value
    assert totals_after["events"] == 1
    assert temp_db._conn.execute("SELECT COUNT(*) FROM proxy_request").fetchone()[0] == 1


def test_rollup_splits_by_executed_stages(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=2)
    llm_id = temp_db.record_proxy_request(_sample_record())
    bm25_id = temp_db.record_proxy_request(
        _sample_record(
            pruning_stages={
                "bm25": StageTokenUsage(
                    model_name="bm25",
                    provider_dns_name="bm25",
                    provider="bm25",
                    usage_source="local:bm25",
                ),
            },
        ),
    )
    _set_proxy_ts(temp_db, llm_id, ts)
    _set_proxy_ts(temp_db, bm25_id, ts)

    temp_db.rollup_historical(today=date.today())
    assert temp_db._conn.execute("SELECT COUNT(*) FROM proxy_request").fetchone()[0] == 2


def test_rollup_separates_has_error(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=2)
    ok_id = temp_db.record_proxy_request(_sample_record())
    err_id = temp_db.record_proxy_request(_sample_record(error="failed"))
    _set_proxy_ts(temp_db, ok_id, ts)
    _set_proxy_ts(temp_db, err_id, ts)

    temp_db.rollup_historical(today=date.today())
    rows = temp_db._conn.execute(
        "SELECT has_error, error FROM proxy_request ORDER BY has_error",
    ).fetchall()
    assert rows == [(0, None), (1, "failed")]


def test_rollup_leaves_today_untouched(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=0)
    ids = []
    for _ in range(2):
        proxy_id = temp_db.record_proxy_request(_sample_record())
        _set_proxy_ts(temp_db, proxy_id, ts)
        ids.append(proxy_id)

    temp_db.rollup_historical(today=date.today())
    assert temp_db._conn.execute("SELECT COUNT(*) FROM proxy_request").fetchone()[0] == 2


def test_rollup_keeps_token_keys_separate(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=2)
    first = temp_db.record_proxy_request(_sample_record(tools_in=100, tools_out=80))
    second = temp_db.record_proxy_request(_sample_record(tools_in=200, tools_out=160))
    _set_proxy_ts(temp_db, first, ts)
    _set_proxy_ts(temp_db, second, ts)

    temp_db.rollup_historical(today=date.today())
    token_rows = temp_db._conn.execute(
        """
        SELECT m.stage, t.type, t.is_saved, t.tokenizer_used, t.tokens
        FROM tokens t
        JOIN model_request m ON m.id = t.model_request_id
        ORDER BY m.stage, t.type, t.is_saved, t.tokenizer_used
        """,
    ).fetchall()
    tools_rows = [row for row in token_rows if row[0] == "tools"]
    assert ("tools", "input", 0, "tiktoken:cl100k_base", 300) in tools_rows
    assert ("tools", "output", 0, "tiktoken:cl100k_base", 240) in tools_rows
    assert temp_db.query_totals()["tools_accepted"] == 300
    assert temp_db.query_totals()["tools_sent_upstream"] == 240


def test_rollup_is_idempotent(temp_db: StatsDB) -> None:
    ts = _ts_ms(days_ago=2)
    for _ in range(2):
        proxy_id = temp_db.record_proxy_request(_sample_record())
        _set_proxy_ts(temp_db, proxy_id, ts)

    first = temp_db.rollup_historical(today=date.today())
    second = temp_db.rollup_historical(today=date.today())
    assert first.groups_merged == 1
    assert second.groups_merged == 0
    assert second.rows_removed == 0


def test_backup_database(temp_db: StatsDB) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        temp_db.close()
        db = StatsDB.init(db_path)
        try:
            db.record_proxy_request(_sample_record())
            backup_path = StatsDB.backup_database(db_path)
            assert Path(backup_path).exists()
            assert backup_path != db_path
        finally:
            db.close()


def test_find_today_backup(temp_db: StatsDB) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        temp_db.close()
        db = StatsDB.init(db_path)
        try:
            assert StatsDB.find_today_backup(db_path) is None
            backup_path = StatsDB.backup_database(db_path)
            assert StatsDB.find_today_backup(db_path) == backup_path
            assert (
                StatsDB.find_today_backup(db_path, today=date.today() - timedelta(days=1)) is None
            )
        finally:
            db.close()


def test_list_backups_ignores_non_backup_files(temp_db: StatsDB) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        temp_db.close()
        db = StatsDB.init(db_path)
        try:
            db.record_proxy_request(_sample_record())
            backup_path = StatsDB.backup_database(db_path)
            decoy = Path(tmp) / "stats_notes.db"
            decoy.write_text("not a backup", encoding="utf-8")

            backups = StatsDB.list_backups(db_path)
            assert [str(path) for path in backups] == [backup_path]
        finally:
            db.close()


def test_prune_old_backups_keeps_latest_ten(temp_db: StatsDB) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "stats.db")
        temp_db.close()
        db = StatsDB.init(db_path)
        try:
            db.record_proxy_request(_sample_record())
            created: list[str] = []
            for day in range(12):
                stamp = f"202501{day + 1:02d}_120000"
                dest = Path(tmp) / f"stats_{stamp}.db"
                shutil.copy2(db_path, dest)
                created.append(str(dest))

            removed = StatsDB.prune_old_backups(db_path, max_backups=10)
            remaining = [str(path) for path in StatsDB.list_backups(db_path)]

            assert len(removed) == 2
            assert removed == created[:2]
            assert remaining == created[2:]
        finally:
            db.close()


def test_journal_mode_is_truncate(temp_db: StatsDB) -> None:
    mode = temp_db._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "truncate"


def test_vacuum_reclaims_freed_pages(temp_db: StatsDB) -> None:
    db_path = temp_db._conn.execute("PRAGMA database_list").fetchone()[2]
    for _ in range(40):
        temp_db.record_proxy_request(_sample_record())
    size_with_data = Path(str(db_path)).stat().st_size
    temp_db._conn.executescript(
        """
        DELETE FROM tokens;
        DELETE FROM model_request;
        DELETE FROM proxy_request;
        """,
    )
    temp_db._conn.commit()
    size_after_delete = Path(str(db_path)).stat().st_size
    assert size_after_delete >= size_with_data * 0.5
    temp_db.vacuum()
    size_after_vacuum = Path(str(db_path)).stat().st_size
    assert size_after_vacuum < size_after_delete
