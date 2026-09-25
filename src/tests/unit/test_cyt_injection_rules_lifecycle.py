"""Unit tests for cyt-injection.mdc session lifecycle (placeholder + prompt injection)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from cyt_client.rules_file import reset_rules_file_rel_path
from tests.support.cyt_injection_rules_lifecycle_fixtures import (
    assert_placeholder_body,
    assert_placeholder_text,
    before_submit_payload,
    build_hook_config,
    lifecycle_payload,
    load_lifecycle_scenario,
    materialize_lifecycle_workspace,
    patch_hook_environment,
    read_rules_text,
    reset_catalog_state,
    resolve_prompt_scenario,
    rules_path_for,
    run_local_hook_inject,
    write_substantive_rules,
)


@pytest.fixture(autouse=True)
def _isolate_lifecycle_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("cyt.hook.active_workspace.touch_active_workspace", lambda *_a, **_k: None)
    monkeypatch.setenv("CYT_HOOK_QUIET", "1")
    reset_rules_file_rel_path()
    reset_catalog_state()
    yield
    reset_rules_file_rel_path()
    reset_catalog_state()


def _run_cyt_client_main(payload: dict[str, object]) -> None:
    from cyt_client.cli import main

    with patch("sys.stdin.buffer.read", return_value=json.dumps(payload).encode()):
        main()


def _run_lifecycle_event(workspace: Path, event: str) -> None:
    payload = lifecycle_payload(workspace, event)
    with patch("cyt_client.cli.post_hook_inject") as post:
        _run_cyt_client_main(payload)
        post.assert_not_called()


def _run_before_submit_with_local_hook(
    workspace: Path,
    *,
    prompt: str,
    config: dict[str, object],
) -> None:
    payload = before_submit_payload(workspace, prompt)

    def _post_hook(_url: str, body: bytes, **_kwargs: object) -> tuple[int, bytes]:
        hook_payload = json.loads(body)
        assert isinstance(hook_payload, dict)
        return 200, run_local_hook_inject(hook_payload, config)

    with patch(
        "cyt_client.cli._resolve_hook_url_for_submit",
        return_value="http://127.0.0.1:8834/hook/inject",
    ):
        with patch("cyt_client.cli.post_hook_inject", side_effect=_post_hook):
            _run_cyt_client_main(payload)


def test_session_start_writes_placeholder_when_rules_file_absent(
    tmp_path: Path,
) -> None:
    workspace = materialize_lifecycle_workspace(tmp_path)
    assert not rules_path_for(workspace).is_file()

    _run_lifecycle_event(workspace, "sessionStart")

    rules_text = read_rules_text(workspace)
    assert_placeholder_text(rules_text)
    assert_placeholder_body(rules_text.split("---", 2)[-1].strip())


def test_session_end_resets_substantive_to_placeholder(tmp_path: Path) -> None:
    workspace = materialize_lifecycle_workspace(tmp_path)
    write_substantive_rules(workspace)

    _run_lifecycle_event(workspace, "sessionEnd")

    assert_placeholder_text(read_rules_text(workspace))


def test_before_submit_syncs_real_hook_tools_to_rules_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_lifecycle_scenario("before_submit_populates_tools")
    workspace = materialize_lifecycle_workspace(tmp_path)
    config = build_hook_config(workspace, db_path=tmp_path / "tiers.db")
    patch_hook_environment(monkeypatch, workspace, config)

    _run_before_submit_with_local_hook(
        workspace,
        prompt=str(scenario.raw["prompt"]),
        config=config,
    )

    rules_text = read_rules_text(workspace)
    assert "alwaysApply: true" in rules_text
    for marker in scenario.raw["expected_rules_markers"]:
        assert marker in rules_text
    for tool_name in scenario.raw["expected_tool_names"]:
        assert tool_name in rules_text


def test_before_submit_syncs_tools_and_skills_when_both_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_lifecycle_scenario("before_submit_populates_tools_and_skills")
    workspace = materialize_lifecycle_workspace(tmp_path, with_skills=True)
    config = build_hook_config(
        workspace,
        db_path=tmp_path / "tiers.db",
        skills_enabled=True,
    )
    patch_hook_environment(monkeypatch, workspace, config, isolate_client_skills=True)

    _run_before_submit_with_local_hook(
        workspace,
        prompt=str(scenario.raw["prompt"]),
        config=config,
    )

    rules_text = read_rules_text(workspace)
    for marker in scenario.raw["expected_rules_markers"]:
        assert marker in rules_text
    for tool_name in scenario.raw["expected_tool_names"]:
        assert tool_name in rules_text


def test_full_session_lifecycle_placeholder_then_inject_then_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = load_lifecycle_scenario("full_session_lifecycle")
    prompt_scenario = resolve_prompt_scenario(scenario)
    workspace = materialize_lifecycle_workspace(tmp_path)
    config = build_hook_config(workspace, db_path=tmp_path / "tiers.db")
    patch_hook_environment(monkeypatch, workspace, config)

    _run_lifecycle_event(workspace, "sessionStart")
    assert_placeholder_text(read_rules_text(workspace))

    _run_before_submit_with_local_hook(
        workspace,
        prompt=str(prompt_scenario.raw["prompt"]),
        config=config,
    )
    rules_after_prompt = read_rules_text(workspace)
    assert "<agent-tools" in rules_after_prompt
    for tool_name in prompt_scenario.raw["expected_tool_names"]:
        assert tool_name in rules_after_prompt

    _run_lifecycle_event(workspace, "sessionEnd")
    assert_placeholder_text(read_rules_text(workspace))
