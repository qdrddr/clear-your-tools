"""Manual BM25 tool-examples query report (fixture-backed, not default CI).

Run:
  uv run pytest src/tests/qa/test_tool_examples_bm25_query.py -m qa --run-qa \\
    --tool-examples-query-id=04_oauth_gitnexus -s

Or use the standalone script:
  uv run src/tests/qa/tool_examples_query.py 04_oauth_gitnexus
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cyt.tool_examples.report import format_query_score_report
from tests.support.tool_examples_fixtures import (
    install_staggered_capture_clock,
    run_query_score_report,
)

pytestmark = pytest.mark.qa


def test_tool_examples_query_report(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query_id = request.config.getoption("--tool-examples-query-id")
    if not query_id:
        pytest.skip("pass --tool-examples-query-id=04_oauth_gitnexus to run this report")
    install_staggered_capture_clock(monkeypatch)
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    db_path = tmp_path / "tool_examples.db"
    report = run_query_score_report(str(query_id), workspace=workspace, db_path=db_path)
    output = format_query_score_report(report)
    print(output)
    assert report.tools, "expected at least one tool in the score report"
    assert any(tool.properties for tool in report.tools), "expected scored property paths"
