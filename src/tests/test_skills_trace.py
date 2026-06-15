"""Tests for skills search trace printing."""

from __future__ import annotations

import pytest

from cyt.skills.diagnostics import BudgetItemRow, SearchItemRow, SkillsSearchTrace
from cyt.skills.search import SkillsPipelineRun
from cyt.skills.trace import print_skills_search_trace


def _trace_with_budget_rows() -> SkillsSearchTrace:
    return SkillsSearchTrace(
        frontmatter_limit=0.4,
        frontmatter_rows=[],
        pipeline_run=SkillsPipelineRun("rerank", "rerank"),
        search_item_kind="node",
        search_score_threshold=0.003,
        search_rows=[
            SearchItemRow("/tmp/a.md", "a", "1", "node", 0.9, True),
            SearchItemRow("/tmp/b.md", "b", "2", "node", 0.03, True),
        ],
        matches=[],
        inject_budget_max=480,
        budget_rows=[
            BudgetItemRow("/tmp/a.md", "1", "node", 0.9, 468, True),
            BudgetItemRow("/tmp/b.md", "2", "node", 0.03, 350, False),
        ],
    )


def test_print_budget_trace_in_debug_shows_dropped_nodes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_skills_search_trace(_trace_with_budget_rows(), debug=True)
    err = capsys.readouterr().err
    assert "skills.inject budget: 480 tokens" in err
    assert "pass when wrapped tokens fit within 480" in err
    assert "file" in err
    assert "/tmp/a.md" in err
    assert "/tmp/b.md" in err
    assert "468" in err
    assert "350" in err
    assert "0.9000" in err
    assert "0.0300" in err
    assert err.count("below") == 1
    assert err.count("pass") >= 1


def test_print_budget_trace_non_debug_hides_dropped_nodes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_skills_search_trace(_trace_with_budget_rows(), debug=False)
    err = capsys.readouterr().err
    assert "skills.inject budget: 480 tokens" in err
    assert "/tmp/a.md" in err
    assert "/tmp/b.md" not in err
    assert "below" not in err
