"""RRF + usage + diversity tool example injection regression against ranking fixtures.

Input fixtures (``fixtures/tool_examples/input/``):

- ``ranking_captures.json`` — captures with optional ``repeat`` for usage weighting
- ``ranking_queries.json`` — queries for city diversity and usage-weighted repo ranking
- ``ranking_tools.json`` — minimal geo_lookup tool (not in the main catalog)
- ``ranking_enrich_golden_<query_id>.json`` — expected enriched tools + flattened paths

Catalog tools for ``07_usage_weighted_repo`` come from ``fixtures/cyt_mcp_catalog/input/tools.json``.
Actual output is written to ``fixtures/tool_examples/out/`` on each test run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.tool_examples_fixtures import (
    INPUT_DIR,
    RANKING_CAPTURES_FIXTURE,
    RANKING_QUERIES_FIXTURE,
    RANKING_TOOLS_FIXTURE,
    install_staggered_capture_clock,
    load_json,
    ranking_query_ids,
    run_ranking_enrich_for_query,
    seed_ranking_captures,
    write_ranking_output,
)

assert RANKING_CAPTURES_FIXTURE.is_file(), f"missing fixture: {RANKING_CAPTURES_FIXTURE}"
assert RANKING_QUERIES_FIXTURE.is_file(), f"missing fixture: {RANKING_QUERIES_FIXTURE}"
assert RANKING_TOOLS_FIXTURE.is_file(), f"missing fixture: {RANKING_TOOLS_FIXTURE}"

RANKING_QUERY_IDS = ranking_query_ids()


def _golden_path(query_id: str) -> Path:
    return INPUT_DIR / f"ranking_enrich_golden_{query_id}.json"


@pytest.fixture
def seeded_ranking_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    install_staggered_capture_clock(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = tmp_path / "tool_examples.db"
    seed_ranking_captures(db_path=db_path, workspace=workspace)
    return db_path, workspace


@pytest.mark.parametrize("query_id", RANKING_QUERY_IDS)
def test_tool_examples_ranking_enrich_matches_golden(
    query_id: str,
    seeded_ranking_db: tuple[Path, Path],
) -> None:
    """Enriched tools must match golden schemas, descriptions, and flattened paths."""
    db_path, workspace = seeded_ranking_db
    golden_path = _golden_path(query_id)
    assert golden_path.is_file(), f"missing golden fixture: {golden_path}"
    golden = load_json(golden_path)
    actual_payload = run_ranking_enrich_for_query(
        query_id=query_id,
        db_path=db_path,
        workspace=workspace,
    )
    write_ranking_output(query_id, actual_payload)

    assert actual_payload["query"] == golden["query"]
    assert actual_payload["tool_names"] == golden["tool_names"]
    assert actual_payload["tools"] == golden["tools"]
    assert actual_payload["flattened_examples"] == golden["flattened_examples"]
