"""Tests for skills session cache.db."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt.skills.cache import SessionCacheDB


@pytest.fixture
def temp_cache() -> Generator[SessionCacheDB]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "cache.db")
        with patch("cyt.skills.cache.skills_cache_db_path", return_value=db_path):
            db = SessionCacheDB.open()
            yield db
            db.close()


def test_upsert_and_lookup(temp_cache: SessionCacheDB) -> None:
    temp_cache.upsert_session("sess-1", "claude-sonnet-4")
    assert temp_cache.lookup_model("sess-1") == "claude-sonnet-4"

    temp_cache.upsert_session("sess-1", "claude-haiku-4")
    assert temp_cache.lookup_model("sess-1") == "claude-haiku-4"


def test_lookup_missing_returns_none(temp_cache: SessionCacheDB) -> None:
    assert temp_cache.lookup_model("missing") is None


def test_purge_stale_sessions(temp_cache: SessionCacheDB) -> None:
    old_ts = int(time.time() * 1000) - (2 * 86_400_000)
    temp_cache._conn.execute(
        "INSERT INTO session (id, model, ts_ms) VALUES (?, ?, ?)",
        ("old", "model-a", old_ts),
    )
    temp_cache._conn.commit()
    temp_cache.upsert_session("fresh", "model-b")

    removed = temp_cache.purge_stale(max_age_ms=86_400_000)
    assert removed >= 1
    assert temp_cache.lookup_model("old") is None
    assert temp_cache.lookup_model("fresh") == "model-b"
