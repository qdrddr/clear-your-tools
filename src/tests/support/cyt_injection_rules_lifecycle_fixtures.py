"""Fixtures for cyt-injection.mdc session lifecycle tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cyt.skills.cli import run_hook_payload
from cyt.skills.hook_payload import normalize_hook_payload
from cyt_client.rules_file import (
    RULES_REL_PATH,
    build_rules_mdc,
    build_rules_mdc_placeholder,
    is_rules_placeholder_body,
    read_cursor_rules_injection,
    rules_file_path,
)
from tests.support.cyt_mcp_catalog_resilience_fixtures import (
    capture_registry_registrations,
    cyt_mcp_hook_config,
    load_ws_tools_catalog,
    materialize_workspace,
    patch_daemon_catalog_status,
    register_ws_catalog,
    reset_catalog_state,
)
import shutil

from tests.support.skills_helpers import isolated_skills_agents_block
from tests.support.tier_behavior_fixtures import SKILLS_SOURCE_ROOT

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "cyt_injection_rules_lifecycle"
SCENARIOS_PATH = FIXTURES_DIR / "scenarios.json"
_PLACEHOLDER_BODY = "Re-read this file as it constantly updates."
_CONVERSATION_ID = "cyt-injection-lifecycle-test"


@dataclass(frozen=True)
class LifecycleScenario:
    id: str
    description: str
    raw: dict[str, Any]


def load_lifecycle_scenarios(path: Path = SCENARIOS_PATH) -> list[LifecycleScenario]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError(f"{path}: expected scenarios array")
    loaded: list[LifecycleScenario] = []
    for item in scenarios:
        if not isinstance(item, dict):
            continue
        scenario_id = str(item.get("id") or "").strip()
        if not scenario_id:
            continue
        loaded.append(
            LifecycleScenario(
                id=scenario_id,
                description=str(item.get("description") or ""),
                raw=dict(item),
            ),
        )
    return loaded


def load_lifecycle_scenario(scenario_id: str, path: Path = SCENARIOS_PATH) -> LifecycleScenario:
    for scenario in load_lifecycle_scenarios(path):
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(f"no cyt-injection lifecycle scenario for id {scenario_id!r}")


def resolve_prompt_scenario(scenario: LifecycleScenario) -> LifecycleScenario:
    prompt_id = scenario.raw.get("prompt_scenario_id")
    if isinstance(prompt_id, str) and prompt_id.strip():
        return load_lifecycle_scenario(prompt_id.strip())
    return scenario


def materialize_lifecycle_workspace(
    tmp_path: Path,
    *,
    with_skills: bool = False,
    skill_fixture_name: str = "context7.md",
) -> Path:
    workspace = materialize_workspace(tmp_path)
    if with_skills:
        skills_root = workspace / ".agents" / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        doc_id = skill_fixture_name.replace(".md", "")
        target_dir = skills_root / doc_id
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SKILLS_SOURCE_ROOT / skill_fixture_name, target_dir / "SKILL.md")
    return workspace


def register_tools_catalog(workspace: Path) -> None:
    register_ws_catalog(workspace, load_ws_tools_catalog())


def build_hook_config(
    workspace: Path,
    *,
    db_path: Path | None = None,
    skills_enabled: bool = False,
) -> dict[str, Any]:
    config = cyt_mcp_hook_config(workspace, db_path=db_path)
    if skills_enabled:
        catalog_dir = workspace / ".cyt" / "skills-catalog"
        catalog_dir.mkdir(parents=True, exist_ok=True)
        config["skills"] = {
            "enabled": True,
            "pipeline": "bm25",
            "catalog_dir": str(catalog_dir),
            "directories": [str(workspace / ".agents" / "skills")],
            "frontmatter_upper_limit": 0.4,
            "max_tokens_per_request": 4000,
            "pageindex": {"enable_bm25_chunking": True},
            "tiers": {"mode": "shadow"},
        }
        config["agents"] = isolated_skills_agents_block()
    return config


def run_local_hook_inject(payload: dict[str, Any], config: dict[str, Any]) -> bytes:
    normalized = normalize_hook_payload(payload)
    result = run_hook_payload(normalized, config, request_payload=payload)
    return result.stdout_text.encode("utf-8")


def rules_path_for(workspace: Path) -> Path:
    return rules_file_path(workspace)


def read_rules_text(workspace: Path) -> str:
    path = rules_path_for(workspace)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def read_rules_body(workspace: Path) -> str:
    return read_cursor_rules_injection(workspace)


def assert_placeholder_text(rules_text: str) -> None:
    assert rules_text == build_rules_mdc_placeholder()


def assert_placeholder_body(body: str) -> None:
    assert is_rules_placeholder_body(body)
    assert _PLACEHOLDER_BODY in body


def write_substantive_rules(
    workspace: Path,
    injection: str = "<agent-tools><cyt-mcp-ws><tool name='demo_tool'></tool></cyt-mcp-ws></agent-tools>",
) -> Path:
    path = rules_path_for(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_rules_mdc(injection), encoding="utf-8")
    return path


def lifecycle_payload(
    workspace: Path,
    event: str,
    *,
    prompt: str = "",
    conversation_id: str = _CONVERSATION_ID,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "hook_event_name": event,
        "conversation_id": conversation_id,
        "workspace_roots": [str(workspace)],
        "cyt_agent": "cursor",
    }
    if prompt:
        payload["prompt"] = prompt
    return payload


def before_submit_payload(workspace: Path, prompt: str) -> dict[str, Any]:
    payload = lifecycle_payload(
        workspace,
        "beforeSubmitPrompt",
        prompt=prompt,
    )
    payload["model"] = "claude-sonnet-4-20250514"
    return payload


def patch_hook_environment(
    monkeypatch: Any,
    workspace: Path,
    config: dict[str, Any],
    *,
    isolate_client_skills: bool = False,
) -> None:
    register_tools_catalog(workspace)
    patch_daemon_catalog_status(monkeypatch, capture_registry_registrations())
    monkeypatch.chdir(workspace)
    monkeypatch.setattr("cyt.config.load_config", lambda: config)
    if isolate_client_skills:
        monkeypatch.setattr(
            "cyt_client.transcript.attach_client_skills",
            lambda data: data,
        )


__all__ = [
    "LifecycleScenario",
    "assert_placeholder_body",
    "assert_placeholder_text",
    "before_submit_payload",
    "build_hook_config",
    "lifecycle_payload",
    "load_lifecycle_scenario",
    "load_lifecycle_scenarios",
    "materialize_lifecycle_workspace",
    "patch_hook_environment",
    "read_rules_body",
    "read_rules_text",
    "register_tools_catalog",
    "reset_catalog_state",
    "resolve_prompt_scenario",
    "run_local_hook_inject",
    "rules_path_for",
    "write_substantive_rules",
    "RULES_REL_PATH",
    "SCENARIOS_PATH",
]
