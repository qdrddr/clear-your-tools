"""Gherkin steps for cross-project tool example isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.support.tool_examples_project_isolation_fixtures import (
    TwoProjectPack,
    enrich_for_workspace,
    foreign_project_slugs_in_examples,
    materialize_two_project_pack,
    record_capture,
    tool_spec,
)
from tests.unit.gherkin.conftest import GherkinContext

FEATURES = Path(__file__).resolve().parent / "features" / "tool_examples_project_isolation.feature"
scenarios(str(FEATURES))

pytestmark = pytest.mark.gherkin


def _pack(gherkin_context: GherkinContext) -> TwoProjectPack:
    pack = gherkin_context.payload.get("pack")
    assert isinstance(pack, TwoProjectPack)
    return pack


@given("two git projects sharing one tool examples database")
def given_two_projects(tmp_path: Path, gherkin_context: GherkinContext) -> None:
    gherkin_context.payload["pack"] = materialize_two_project_pack(tmp_path)


@given(parsers.re(r"repo A has a recorded (?P<scope>workspace-scoped|user-scoped) .+ capture"))
def given_repo_a_recorded_capture(
    scope: str,
    gherkin_context: GherkinContext,
) -> None:
    tool_key = "workspace_scoped" if scope == "workspace-scoped" else "user_scoped"
    pack = _pack(gherkin_context)
    spec = tool_spec(tool_key)
    record_capture(
        workspace=pack.repo_a,
        spec=spec,
        args=spec.repo_a_capture,
        config=pack.base_config,
    )
    gherkin_context.payload["tool_key"] = tool_key
    gherkin_context.payload["spec"] = spec


@when(
    parsers.re(r"repo B enriches .+ for a (?P<prompt_kind>workspace search|documentation) prompt"),
)
def when_repo_b_enriches(
    prompt_kind: str,
    gherkin_context: GherkinContext,
) -> None:
    tool_key = str(gherkin_context.payload.get("tool_key") or "")
    if not tool_key:
        tool_key = "workspace_scoped" if prompt_kind == "workspace search" else "user_scoped"
    spec = tool_spec(tool_key)
    pack = _pack(gherkin_context)
    examples = enrich_for_workspace(
        workspace=pack.repo_b,
        spec=spec,
        query=spec.enrich_query,
        config=pack.base_config,
    )
    gherkin_context.payload["examples"] = examples
    gherkin_context.payload["tool_key"] = tool_key


@then("repo B should have no injected examples")
def then_repo_b_has_no_examples(gherkin_context: GherkinContext) -> None:
    examples = gherkin_context.payload.get("examples")
    assert isinstance(examples, list)
    assert examples == []


@then("repo B examples must not reference repo A")
def then_repo_b_has_no_foreign_refs(gherkin_context: GherkinContext) -> None:
    pack = _pack(gherkin_context)
    examples = gherkin_context.payload.get("examples")
    assert isinstance(examples, list)
    assert (
        foreign_project_slugs_in_examples(
            examples,
            own_slug=pack.repo_b_slug,
            other_slug=pack.repo_a_slug,
        )
        == []
    )
