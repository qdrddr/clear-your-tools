"""Unit tests: tool example captures and enrich stay scoped per git project."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.tool_examples_project_isolation_fixtures import (
    enrich_for_workspace,
    foreign_project_slugs_in_examples,
    materialize_two_project_pack,
    project_ids,
    record_capture,
    tool_spec,
)


@pytest.mark.parametrize("tool_key", ["workspace_scoped", "user_scoped"])
def test_record_stores_captures_under_matching_project_only(
    tmp_path: Path,
    tool_key: str,
) -> None:
    pack = materialize_two_project_pack(tmp_path)
    spec = tool_spec(tool_key)
    config_a = pack.base_config

    assert (
        record_capture(
            workspace=pack.repo_a,
            spec=spec,
            args=spec.repo_a_capture,
            config=config_a,
        )
        is not None
    )
    assert (
        record_capture(
            workspace=pack.repo_b,
            spec=spec,
            args=spec.repo_b_capture,
            config=config_a,
        )
        is not None
    )

    project_id_a, project_id_b = project_ids(pack.db_path, pack.repo_a, pack.repo_b)
    assert project_id_a != project_id_b


@pytest.mark.parametrize("tool_key", ["workspace_scoped", "user_scoped"])
def test_enrich_does_not_load_other_project_captures(
    tmp_path: Path,
    tool_key: str,
) -> None:
    pack = materialize_two_project_pack(tmp_path)
    spec = tool_spec(tool_key)

    record_capture(
        workspace=pack.repo_a,
        spec=spec,
        args=spec.repo_a_capture,
        config=pack.base_config,
    )

    examples = enrich_for_workspace(
        workspace=pack.repo_b,
        spec=spec,
        query=spec.enrich_query,
        config=pack.base_config,
    )
    assert examples == []
    assert (
        foreign_project_slugs_in_examples(
            examples,
            own_slug=pack.repo_b_slug,
            other_slug=pack.repo_a_slug,
        )
        == []
    )


@pytest.mark.parametrize("tool_key", ["workspace_scoped", "user_scoped"])
def test_enrich_loads_only_current_project_captures(
    tmp_path: Path,
    tool_key: str,
) -> None:
    pack = materialize_two_project_pack(tmp_path)
    spec = tool_spec(tool_key)

    record_capture(
        workspace=pack.repo_a,
        spec=spec,
        args=spec.repo_a_capture,
        config=pack.base_config,
    )
    record_capture(
        workspace=pack.repo_b,
        spec=spec,
        args=spec.repo_b_capture,
        config=pack.base_config,
    )

    examples_a = enrich_for_workspace(
        workspace=pack.repo_a,
        spec=spec,
        query=spec.enrich_query,
        config=pack.base_config,
    )
    examples_b = enrich_for_workspace(
        workspace=pack.repo_b,
        spec=spec,
        query=spec.enrich_query,
        config=pack.base_config,
    )

    assert examples_a
    assert examples_b
    assert examples_a != examples_b
    assert (
        foreign_project_slugs_in_examples(
            examples_a,
            own_slug=pack.repo_a_slug,
            other_slug=pack.repo_b_slug,
        )
        == []
    )
    assert (
        foreign_project_slugs_in_examples(
            examples_b,
            own_slug=pack.repo_b_slug,
            other_slug=pack.repo_a_slug,
        )
        == []
    )
