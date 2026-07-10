#!/usr/bin/env python3
"""Minimal LLM pruning integration harness (imports only — no HTTP).

Two paths, both without a running hook daemon or ``cyt-client`` HTTP:

1. **Selector** — calls ``cyt.pruners.llm`` / ``cyt.skills.llm`` directly and prints
   system/user prompts sent to the pruning LLM.
2. **Hook daemon** — imports ``cyt.hook.http_server`` + ``cyt.skills.cli`` and calls
   ``run_hook_payload`` (same entry as ``POST /hook/inject``) with patched minimal
   tool/skill catalogs.

Run all scenarios (requires configured pruning LLM + API keys):
    OPENROUTER_API_KEY="$(security find-generic-password -s "nono" -a "OPENROUTER_API_KEY" -w)"

    uv run pytest src/tests/test_llm_prune_integration.py -s

Run one scenario from the CLI:

    uv run python src/tests/test_llm_prune_integration.py --mode tools --agent cursor --rule .debug/rules/cyt-injection.mdc \
        --prompt "resolve Next.js library id"
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal
from unittest.mock import patch

import pytest

from cyt.common.agents import AgentName
from cyt.common.paths import shorten_home_path
from cyt.common.token_usage import StageTokenUsage, empty_usage
from cyt.config import load_config, require_proxy_env
from cyt.hook import http_server as cyt_hook_http_server
from cyt.launch.upstream import parse_agent_name
from cyt.pruners.llm import (
    TOOL_SELECTOR_SYSTEM_PROMPT,
    _llm_user_message,
    apply_selector_ids_to_catalog,
    llm_select_ids,
    llm_selector_bulk_base_tokens,
    prepare_catalog_selector_chunks,
    tool_selector_system_prompt,
)
from cyt.pruners.selector_xml import (
    SELECTOR_SOFT_BUDGET_MIN,
    SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
    SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
    format_skills_selector_token_metadata,
    format_tools_selector_token_metadata,
    per_bulk_soft_budget,
)
from cyt.pruners.split import split_into_bulks
from cyt.skills.catalog import SkillEntryRef, _iter_content_node_ids
from cyt.skills.cli import HookRunResult, run_hook_payload
from cyt.skills.hook_payload import normalize_hook_payload
from cyt.skills.inject import format_agent_skills
from cyt.skills.llm import (
    SKILLS_SELECTOR_SYSTEM_PROMPT,
    prepare_skill_nodes,
    reconstruct_skills_from_llm_ids,
    skills_selector_system_prompt,
)
from cyt.skills.search import MatchedSkill
from cyt.skills.transcript import skills_search_query_from_hook_payload
from cyt.tools.inject import format_agent_tools
from cyt_client.agent import (
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CYT_LAUNCH_AGENT_ENV,
)
from cyt_client.rules_file import (
    delete_cursor_rules_file,
    extract_additional_context,
    injection_section_for_domain,
    reset_rules_file_rel_path,
    rules_file_path,
    set_rules_file_rel_path,
    sync_cursor_rules_file,
    workspace_root_from_payload,
)
from cyt_client.transcript import enrich_hook_payload

ScenarioMode = Literal["tools", "skills", "combined"]
SelectorDomain = Literal["tools", "skills"]

DEFAULT_AGENT: AgentName = "cursor"

_HARNESS_ENV_VARS = (
    CODEX_HOME_ENV,
    CURSOR_VERSION_ENV,
    CLAUDE_PROJECT_DIR_ENV,
    CLAUDECODE_ENV,
    CLAUDE_CODE_ENTRYPOINT_ENV,
    CYT_LAUNCH_AGENT_ENV,
)

DEFAULT_TOOL_JSON = Path(
    "/Users/dberezenko/.config/cyt/tools/entries/"
    "f913b7ff3274a796c120a5259cee62001e23d268411b923a77d203a3a837bd10/"  # pragma: allowlist secret
    "schemas/decomposed/tools.context7_mcp.org.localcontext7mcp.resolve_library_id.json",
)
DEFAULT_SKILL_ENTRY_DIR = Path(
    "/Users/dberezenko/.config/cyt/skills/entries/"
    "4b4fecc8233152c00af6da0278fd66ed0c00f5d380ca7148cbcd3861f27723a3",  # pragma: allowlist secret
)
DEFAULT_SKILL_NODE_ID = 7
DEFAULT_USER_PROMPT = (
    "How do I use ctx_patch for anchored file editing? "
    "Also show me how to resolve a Next.js library id with Context7."
)


@dataclass(frozen=True)
class SelectorBulkPlanEntry:
    bulk_index: int
    item_count: int
    cached_token_total: int
    soft_budget: int


@dataclass(frozen=True)
class SelectorBulkPlan:
    domain: SelectorDomain
    soft_budget_total: int
    num_bulks: int
    per_bulk_soft_budget: int
    multiple_batches: bool
    bulks: tuple[SelectorBulkPlanEntry, ...]


@dataclass(frozen=True)
class SelectorLegTrace:
    system_prompt: str
    user_prompt: str
    selected_ids: set[int]
    selected_scores: dict[int, int]
    pruned_output: str
    token_usage: StageTokenUsage
    bulk_plan: SelectorBulkPlan
    selector_token_metadata: str = ""


@dataclass(frozen=True)
class LlmPruneTrace:
    mode: ScenarioMode
    agent: AgentName
    raw_hook_payload: dict[str, Any]
    enriched_hook_payload: dict[str, Any]
    query: str
    system_prompt: str
    user_prompt: str
    selected_ids: set[int]
    selected_scores: dict[int, int]
    pruned_output: str
    token_usage: StageTokenUsage
    selector_skill_matches: tuple[MatchedSkill, ...] = ()
    tools_selector: SelectorLegTrace | None = None
    skills_selector: SelectorLegTrace | None = None


@dataclass(frozen=True)
class HookDaemonTrace:
    mode: ScenarioMode
    agent: AgentName
    raw_hook_payload: dict[str, Any]
    enriched_hook_payload: dict[str, Any]
    outcome: str
    stdout_text: str
    details: dict[str, Any] | None
    rules_file: Path | None = None


def _print_section(title: str, body: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n{title}\n{bar}\n{body.rstrip()}\n")


def _format_selector_selections(scores: dict[int, int]) -> str:
    """JSON array of ``{id, score}`` pairs sorted by id (LLM selector contract)."""
    pairs = [{"id": chunk_id, "score": score} for chunk_id, score in sorted(scores.items())]
    return json.dumps(pairs, indent=2)


def _plan_selector_bulks(
    query: str,
    system_prompt: str,
    formatted_items: list[str],
    *,
    chunk_token_counts: list[int] | None,
    domain: SelectorDomain,
    soft_budget_total: int,
    soft_budget_min: int = SELECTOR_SOFT_BUDGET_MIN,
) -> SelectorBulkPlan:
    """Mirror ``llm_select_ids`` batching to surface bulk count and per-bulk soft budgets."""
    if not formatted_items:
        return SelectorBulkPlan(
            domain=domain,
            soft_budget_total=soft_budget_total,
            num_bulks=0,
            per_bulk_soft_budget=soft_budget_total,
            multiple_batches=False,
            bulks=(),
        )

    token_counts = chunk_token_counts
    if token_counts is not None and len(token_counts) != len(formatted_items):
        token_counts = None

    base_tokens = llm_selector_bulk_base_tokens(query, system_prompt)
    item_pairs = list(
        zip(formatted_items, token_counts or [0] * len(formatted_items), strict=True),
    )
    bulks_of_items = split_into_bulks(
        items=item_pairs,
        transform_fn=lambda pair: pair[0],
        base_tokens=base_tokens,
        item_token_counts=token_counts,
    )
    per_bulk = per_bulk_soft_budget(
        soft_budget_total,
        len(bulks_of_items),
        min_budget=soft_budget_min,
    )
    entries = tuple(
        SelectorBulkPlanEntry(
            bulk_index=index + 1,
            item_count=len(bulk),
            cached_token_total=sum(count for _item, count in bulk),
            soft_budget=per_bulk,
        )
        for index, bulk in enumerate(bulks_of_items)
    )
    return SelectorBulkPlan(
        domain=domain,
        soft_budget_total=soft_budget_total,
        num_bulks=len(entries),
        per_bulk_soft_budget=per_bulk,
        multiple_batches=len(entries) > 1,
        bulks=entries,
    )


def _format_bulk_plan(plan: SelectorBulkPlan) -> str:
    lines = [
        f"domain: {plan.domain}",
        f"soft_budget_total: {plan.soft_budget_total}",
        f"num_bulks: {plan.num_bulks}",
        f"multiple_batches: {plan.multiple_batches}",
        f"per_bulk_soft_budget: {plan.per_bulk_soft_budget}",
        "bulks:",
    ]
    for entry in plan.bulks:
        lines.append(
            "  "
            f"bulk {entry.bulk_index}: "
            f"items={entry.item_count}, "
            f"cached_tokens={entry.cached_token_total}, "
            f"soft_budget={entry.soft_budget}",
        )
    return "\n".join(lines)


def _parse_agent(raw: str) -> AgentName:
    return parse_agent_name(raw.strip().lower())


@contextmanager
def _cyt_client_agent_env(agent: AgentName) -> Iterator[None]:
    """Isolate harness env and set ``CYT_LAUNCH_AGENT`` like hooks.json does for cyt-client."""
    saved = {name: os.environ.get(name) for name in _HARNESS_ENV_VARS}
    for name in _HARNESS_ENV_VARS:
        os.environ.pop(name, None)
    os.environ[CYT_LAUNCH_AGENT_ENV] = agent
    try:
        yield
    finally:
        for name in _HARNESS_ENV_VARS:
            os.environ.pop(name, None)
        for name, value in saved.items():
            if value is not None:
                os.environ[name] = value


def agent_raw_hook_payload(
    agent: AgentName,
    *,
    prompt: str,
    cwd: str | None = None,
) -> dict[str, Any]:
    """cyt-client stdin JSON before ``enrich_hook_payload`` (per-agent hook shape)."""
    workspace = cwd or str(Path.cwd())
    if agent == "cursor":
        return {
            "hook_event_name": "beforeSubmitPrompt",
            "prompt": prompt,
            "conversation_id": "integration-test",
            "workspace_roots": [workspace],
            "model": "integration-test",
        }
    if agent == "claude":
        return {
            "hook_event_name": "UserPromptSubmit",
            "prompt": prompt,
            "cwd": workspace,
            "session_id": "integration-test",
        }
    return {
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "cwd": workspace,
        "session_id": "integration-test",
        "model": "integration-test",
    }


def enrich_like_cyt_client(raw: dict[str, Any], *, agent: AgentName) -> dict[str, Any]:
    """Apply cyt-client enrichment: ``cyt_hook_payload``, ``cyt_agent``, skills, etc."""
    with _cyt_client_agent_env(agent):
        enriched = json.loads(enrich_hook_payload(json.dumps(raw).encode()))
    if not isinstance(enriched, dict):
        raise TypeError("enrich_hook_payload must return a JSON object")
    return enriched


@contextmanager
def _rules_path_context(rule_path: str | None) -> Iterator[None]:
    """Mirror ``cyt-client --rule``: set path before enrich reads prior injection."""
    if rule_path and rule_path.strip():
        set_rules_file_rel_path(rule_path.strip())
    try:
        yield
    finally:
        if rule_path and rule_path.strip():
            reset_rules_file_rel_path()


def maybe_sync_cursor_rules_file(
    trace: HookDaemonTrace,
    *,
    rule_path: str | None,
) -> Path | None:
    """Write hook injection to a Cursor rules file (``cyt-client --rule`` behavior)."""
    if not rule_path or not rule_path.strip():
        return None
    if trace.agent != "cursor":
        print(
            f"--rule ignored: only supported with --agent cursor (got {trace.agent})",
            file=sys.stderr,
        )
        return None

    workspace = workspace_root_from_payload(trace.enriched_hook_payload)
    if workspace is None:
        print("--rule: no workspace in hook payload; rules file not updated", file=sys.stderr)
        return None

    injection = extract_additional_context(trace.stdout_text.encode())
    merge_sections = trace.mode == "combined"
    if not injection.strip() and trace.mode in {"tools", "skills"}:
        rules_inj = trace.enriched_hook_payload.get("cyt_rules_injection")
        if isinstance(rules_inj, str):
            injection = injection_section_for_domain(rules_inj, trace.mode)

    if not injection.strip():
        deleted = delete_cursor_rules_file(workspace)
        print(
            f"--rule: hook returned no injection; rules file deleted={deleted}",
            file=sys.stderr,
        )
        return None

    path = rules_file_path(workspace)
    changed = sync_cursor_rules_file(
        workspace,
        injection,
        merge_sections=merge_sections,
    )
    print(f"--rule: wrote {path} (changed={changed})", file=sys.stderr)
    return path


def invoke_hook_inject_local(
    *,
    agent: AgentName,
    prompt: str,
    config: dict[str, Any],
    debug: bool = False,
) -> tuple[HookRunResult, dict[str, Any], dict[str, Any]]:
    """Run ``POST /hook/inject`` logic in-process (no HTTP, no daemon process).

    Mirrors ``cyt-client`` stdin enrichment, then the same ``run_hook_payload`` call as
    ``cyt.hook.http_server._run_hook_in_thread``.
    """
    raw = agent_raw_hook_payload(agent, prompt=prompt)
    enriched = enrich_like_cyt_client(raw, agent=agent)
    normalized = normalize_hook_payload(enriched)
    result = run_hook_payload(
        normalized,
        config,
        request_payload=enriched,
        plain_output=False,
        debug=debug,
        io_guarded=True,
        allow_transcript_file_read=False,
    )
    return result, raw, enriched


def _integration_config(
    base: dict[str, Any] | None = None,
    *,
    mode: ScenarioMode | None = None,
    stats_db: str | None = None,
) -> dict[str, Any]:
    cfg = copy.deepcopy(base or load_config())
    pruning = cfg.setdefault("pruning", {})
    pruning["inject_via"] = "hook"
    tools = pruning.setdefault("tools", {})
    tools["enabled"] = mode in {None, "tools", "combined"}
    tools.setdefault("policy", {})["minimum_tools"] = 1
    tools["sequence"] = ["llm"]
    tools.setdefault("pipelines", {}).setdefault("llm", {})
    skills = cfg.setdefault("skills", {})
    skills["enabled"] = mode in {None, "skills", "combined"}
    skills["pipeline"] = "llm"
    skills.setdefault("max_tokens_per_request", 4000)
    if mode in {"skills", "combined"}:
        # Single-node fixtures must use LLM nodes (same as selector path), not BM25 chunk fallback.
        skills["bm25_node_fallback_threshold"] = 0
    stats = cfg.setdefault("stats", {}).setdefault("database", {})
    if stats_db is not None:
        stats["path"] = stats_db
    return cfg


def load_tool_definition(tool_json_path: Path) -> dict[str, Any]:
    path = tool_json_path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"decomposed tool not found: {path}")
    item = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError(f"expected JSON object in {path}")
    tool = dict(item)
    schema = tool.pop("inputSchema", None) or tool.get("input_schema")
    if schema is not None:
        tool["input_schema"] = schema
    return tool


def load_decomposed_tool_catalog(tool_json_path: Path) -> dict[str, Any]:
    from cyt.pruners.selector_xml import (
        parse_cached_token_count,
        token_count_from_decomposed_metadata,
    )

    path = tool_json_path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"decomposed tool not found: {path}")
    item = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(item, dict):
        raise ValueError(f"expected JSON object in {path}")
    item = dict(item)
    item["file_path"] = shorten_home_path(str(path))
    item.setdefault("score", 1.0)
    token_count = parse_cached_token_count(item)
    if token_count is None:
        token_count = token_count_from_decomposed_metadata(path)
        if token_count is not None:
            item["token_count"] = token_count
    return {"json": [item], "md": [], "tools": []}


def _filter_structure_to_node(structure: list[Any], node_id: int) -> list[Any]:
    for node in structure:
        if not isinstance(node, dict):
            continue
        children = node.get("nodes")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict) and child.get("node_id") == node_id:
                    return [dict(child)]
            continue
        if node.get("node_id") == node_id:
            return [dict(node)]
    return []


def load_single_skill_entry(
    entry_dir: Path,
    *,
    node_id: int,
) -> SkillEntryRef:
    root = entry_dir.expanduser()
    metadata_path = root / "metadata.json"
    page_index_path = root / "nodes" / "page_index.json"
    if not metadata_path.is_file() or not page_index_path.is_file():
        raise FileNotFoundError(f"skill entry cache incomplete: {root}")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    document = json.loads(page_index_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"invalid page_index.json in {root}")

    structure = document.get("structure")
    if not isinstance(structure, list):
        raise ValueError(f"missing structure in {page_index_path}")

    document = dict(document)
    document["structure"] = _filter_structure_to_node(structure, node_id)
    if not _iter_content_node_ids(document["structure"]):
        raise ValueError(f"node_id {node_id} not found in {page_index_path}")

    source_path = str(metadata.get("source_path", document.get("path", root)))
    doc_id = str(document.get("id", "skill"))
    params_hash = "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"  # pragma: allowlist secret
    bm25_chunk_dir = root / "chunks" / "bm25" / params_hash

    return SkillEntryRef(
        source_path=source_path,
        doc_id=doc_id,
        content_sha256=root.name,
        cache_key=root.name,
        entry_dir=str(root),
        nodes_dir=str(root / "nodes"),
        chunk_dir=str(bm25_chunk_dir),
        bm25_chunk_dir=str(bm25_chunk_dir),
        pipeline=str(metadata.get("pipeline", "llm")),
        index_params_hash=params_hash,
        disk_backed=True,
        document=document,
    )


def _catalog_json_item_for_inject(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a decomposed catalog ``json`` row for ``format_tool_item``.

    Decomposed on-disk chunks use MCP's ``inputSchema``; ``format_tool_item`` (and
    the hook inject path after recompose) expect ``input_schema``. Feeding catalog
    rows directly without this step yields ``{'input_schema': {}}`` even when the
    chunk still carries a full schema under ``inputSchema``.
    """
    tool = dict(item)
    schema = tool.pop("inputSchema", None) or tool.get("input_schema") or tool.get("parameters")
    if schema is not None:
        tool["input_schema"] = schema
    return tool


def _format_pruned_tools(catalog: dict[str, Any], selected_ids: set[int]) -> str:
    _chunks, metadata, list_keys, _token_counts, _token_rows = prepare_catalog_selector_chunks(
        catalog,
    )
    pruned = apply_selector_ids_to_catalog(
        copy.deepcopy(catalog),
        metadata,
        selected_ids,
        list_keys,
    )
    inject_tools = [
        _catalog_json_item_for_inject(item)
        for item in pruned.get("json", [])
        if isinstance(item, dict)
    ]
    if inject_tools:
        return format_agent_tools(inject_tools)
    return json.dumps(pruned, indent=2)


def _run_tools_selector_leg(
    query: str,
    *,
    tool_catalog: dict[str, Any],
    config: dict[str, Any],
) -> SelectorLegTrace:
    tool_chunks, _tool_metadata, _, tool_token_counts, tool_token_rows = (
        prepare_catalog_selector_chunks(tool_catalog)
    )
    tools_prompt = tool_selector_system_prompt(config)
    bulk_plan = _plan_selector_bulks(
        query,
        tools_prompt,
        tool_chunks,
        chunk_token_counts=tool_token_counts,
        domain="tools",
        soft_budget_total=SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
    )
    token_metadata = format_tools_selector_token_metadata(
        tool_token_rows,
        bulk_cached_totals=tuple(entry.cached_token_total for entry in bulk_plan.bulks),
    )
    tool_scores, tool_usage = llm_select_ids(
        query,
        tools_prompt,
        tool_chunks,
        chunk_token_counts=tool_token_counts,
        wrap_agent_tools=True,
        system_prompt_for_budget=lambda budget: tool_selector_system_prompt(
            config,
            soft_budget=budget,
        ),
        soft_budget_total=SELECTOR_SOFT_BUDGET_TOOLS_TOTAL,
        config=config,
    )
    tool_text = _format_pruned_tools(tool_catalog, set(tool_scores))
    return SelectorLegTrace(
        system_prompt=tools_prompt,
        user_prompt=_llm_user_message(query, "".join(tool_chunks)),
        selected_ids=set(tool_scores),
        selected_scores=tool_scores,
        pruned_output=tool_text or "(no tools selected)",
        token_usage=tool_usage,
        bulk_plan=bulk_plan,
        selector_token_metadata=token_metadata,
    )


def _run_skills_selector_leg(
    query: str,
    *,
    skill_entries: list[SkillEntryRef],
    config: dict[str, Any],
) -> tuple[SelectorLegTrace, tuple[MatchedSkill, ...]]:
    skill_items, skill_metadata, skill_token_counts, skill_block_rows = prepare_skill_nodes(
        skill_entries,
    )
    bulk_plan = _plan_selector_bulks(
        query,
        SKILLS_SELECTOR_SYSTEM_PROMPT,
        skill_items,
        chunk_token_counts=skill_token_counts,
        domain="skills",
        soft_budget_total=SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
    )
    token_metadata = format_skills_selector_token_metadata(skill_metadata, skill_block_rows)
    selected_scores, usage = llm_select_ids(
        query,
        SKILLS_SELECTOR_SYSTEM_PROMPT,
        skill_items,
        chunk_token_counts=skill_token_counts,
        system_prompt_for_budget=lambda budget: skills_selector_system_prompt(
            soft_budget=budget,
        ),
        soft_budget_total=SELECTOR_SOFT_BUDGET_SKILLS_TOTAL,
        config=config,
    )
    skill_selected_scores = {
        sid: score for sid, score in selected_scores.items() if sid in skill_metadata
    }
    matches = reconstruct_skills_from_llm_ids(
        skill_metadata,
        skill_selected_scores,
        skill_entries,
        config=config,
    )
    pruned_output = format_agent_skills(matches) if matches else "(no skill nodes selected)"
    return (
        SelectorLegTrace(
            system_prompt=SKILLS_SELECTOR_SYSTEM_PROMPT,
            user_prompt=_llm_user_message(query, "".join(skill_items)),
            selected_ids=set(skill_selected_scores),
            selected_scores=skill_selected_scores,
            pruned_output=pruned_output,
            token_usage=usage,
            bulk_plan=bulk_plan,
            selector_token_metadata=token_metadata,
        ),
        tuple(matches),
    )


def _selector_skill_search_side_effect(
    selector_skill_matches: tuple[MatchedSkill, ...],
) -> Callable[..., list[MatchedSkill]]:
    def _search_skills_from_selector(
        *_args: object,
        **_kwargs: object,
    ) -> list[MatchedSkill]:
        return list(selector_skill_matches)

    return _search_skills_from_selector


def _daemon_patch_stack(
    mode: ScenarioMode,
    *,
    tool_json_path: Path,
    skill_entry_dir: Path,
    skill_node_id: int,
    selector_skill_matches: tuple[MatchedSkill, ...] | None = None,
) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(patch("cyt.config.tools_hook_file_missing", return_value=False))
    stack.enter_context(patch("cyt.tools.hook.record_tools_hook_injection"))
    stack.enter_context(patch("cyt.skills.stats.record_skills_injection"))

    if mode in {"tools", "combined"}:
        tool = load_tool_definition(tool_json_path)
        minimal_catalog = [tool]
        stack.enter_context(
            patch("cyt.pruning.hook_bridge.load_tool_catalog", return_value=minimal_catalog),
        )
        # tools-only mode uses cyt.tools.hook.load_tool_catalog (non-coordinated path).
        stack.enter_context(
            patch("cyt.tools.hook.load_tool_catalog", return_value=minimal_catalog),
        )

    if mode in {"skills", "combined"}:
        entry = load_single_skill_entry(skill_entry_dir, node_id=skill_node_id)
        minimal_entries = [entry]

        def _patched_build_registry(
            *_args: object,
            **_kwargs: object,
        ) -> list[SkillEntryRef]:
            return list(minimal_entries)

        stack.enter_context(
            patch(
                "cyt.pruning.hook_bridge.build_registry_for_hook_payload",
                return_value=minimal_entries,
            ),
        )
        # skills-only mode uses cyt.skills.cli.build_registry_for_hook_payload (non-coordinated path).
        stack.enter_context(
            patch(
                "cyt.skills.cli.build_registry_for_hook_payload",
                side_effect=_patched_build_registry,
            ),
        )
        stack.enter_context(
            patch(
                "cyt.pruning.hook_bridge.eligible_skills_after_gate",
                side_effect=lambda _query, entries, **_: entries,
            ),
        )

        if selector_skill_matches is not None:
            selector_side_effect = _selector_skill_search_side_effect(selector_skill_matches)
            stack.enter_context(
                patch("cyt.skills.cli.search_skills", side_effect=selector_side_effect),
            )
            # Combined mode uses the coordinated hook path (proxy_inject -> search.search_skills).
            stack.enter_context(
                patch("cyt.skills.search.search_skills", side_effect=selector_side_effect),
            )
    return stack


def run_hook_daemon_scenario(
    mode: ScenarioMode,
    *,
    prompt: str,
    config: dict[str, Any],
    agent: AgentName = DEFAULT_AGENT,
    tool_json_path: Path = DEFAULT_TOOL_JSON,
    skill_entry_dir: Path = DEFAULT_SKILL_ENTRY_DIR,
    skill_node_id: int = DEFAULT_SKILL_NODE_ID,
    debug: bool = True,
    rule_path: str | None = None,
    selector_trace: LlmPruneTrace | None = None,
) -> HookDaemonTrace:
    """Full hook inject path via daemon server modules (no HTTP)."""
    selector_skill_matches = (
        selector_trace.selector_skill_matches
        if selector_trace is not None and mode in {"skills", "combined"}
        else None
    )
    with _rules_path_context(rule_path):
        with _daemon_patch_stack(
            mode,
            tool_json_path=tool_json_path,
            skill_entry_dir=skill_entry_dir,
            skill_node_id=skill_node_id,
            selector_skill_matches=selector_skill_matches,
        ):
            result, raw, enriched = invoke_hook_inject_local(
                agent=agent,
                prompt=prompt,
                config=config,
                debug=debug,
            )
        trace = HookDaemonTrace(
            mode=mode,
            agent=agent,
            raw_hook_payload=raw,
            enriched_hook_payload=enriched,
            outcome=result.outcome,
            stdout_text=result.stdout_text,
            details=result.details,
        )
        rules_file = maybe_sync_cursor_rules_file(trace, rule_path=rule_path)
        if rules_file is not None:
            trace = replace(trace, rules_file=rules_file)
        return trace


def run_llm_prune_scenario(
    mode: ScenarioMode,
    *,
    prompt: str,
    config: dict[str, Any],
    agent: AgentName = DEFAULT_AGENT,
    tool_json_path: Path = DEFAULT_TOOL_JSON,
    skill_entry_dir: Path = DEFAULT_SKILL_ENTRY_DIR,
    skill_node_id: int = DEFAULT_SKILL_NODE_ID,
) -> LlmPruneTrace:
    raw = agent_raw_hook_payload(agent, prompt=prompt)
    enriched = enrich_like_cyt_client(raw, agent=agent)
    query = skills_search_query_from_hook_payload(enriched, allow_file_read=False) or prompt

    tool_catalog: dict[str, Any] | None = None
    skill_entries: list[SkillEntryRef] = []
    system_prompt = TOOL_SELECTOR_SYSTEM_PROMPT
    user_prompt = ""
    selected_ids: set[int] = set()
    selected_scores: dict[int, int] = {}
    usage = empty_usage()
    pruned_output = ""
    selector_skill_matches: tuple[MatchedSkill, ...] = ()
    tools_selector: SelectorLegTrace | None = None
    skills_selector: SelectorLegTrace | None = None

    if mode == "combined":
        tool_catalog = load_decomposed_tool_catalog(tool_json_path)
        skill_entries = [load_single_skill_entry(skill_entry_dir, node_id=skill_node_id)]
        tools_selector = _run_tools_selector_leg(
            query,
            tool_catalog=tool_catalog,
            config=config,
        )
        skills_selector, selector_skill_matches = _run_skills_selector_leg(
            query,
            skill_entries=skill_entries,
            config=config,
        )
        usage = tools_selector.token_usage.merge(skills_selector.token_usage)
        selected_ids = tools_selector.selected_ids | skills_selector.selected_ids
        selected_scores = {
            **tools_selector.selected_scores,
            **skills_selector.selected_scores,
        }
        system_prompt = f"{tools_selector.system_prompt}\n\n---\n\n{skills_selector.system_prompt}"
        user_prompt = "\n\n---\n\n".join(
            part
            for part in (tools_selector.user_prompt, skills_selector.user_prompt)
            if part.strip()
        )
        pruned_output = (
            "\n\n".join(
                part
                for part in (tools_selector.pruned_output, skills_selector.pruned_output)
                if part.strip() and not part.startswith("(no ")
            )
            or "(empty)"
        )
    else:
        if mode == "tools":
            tool_catalog = load_decomposed_tool_catalog(tool_json_path)
            tools_selector = _run_tools_selector_leg(
                query,
                tool_catalog=tool_catalog,
                config=config,
            )
            system_prompt = tools_selector.system_prompt
            user_prompt = tools_selector.user_prompt
            selected_ids = tools_selector.selected_ids
            selected_scores = tools_selector.selected_scores
            usage = tools_selector.token_usage
            pruned_output = tools_selector.pruned_output

        if mode == "skills":
            skill_entries = [load_single_skill_entry(skill_entry_dir, node_id=skill_node_id)]
            skills_selector, selector_skill_matches = _run_skills_selector_leg(
                query,
                skill_entries=skill_entries,
                config=config,
            )
            system_prompt = skills_selector.system_prompt
            user_prompt = skills_selector.user_prompt
            selected_ids = skills_selector.selected_ids
            selected_scores = skills_selector.selected_scores
            usage = skills_selector.token_usage
            pruned_output = skills_selector.pruned_output

    return LlmPruneTrace(
        mode=mode,
        agent=agent,
        raw_hook_payload=raw,
        enriched_hook_payload=enriched,
        query=query,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        selected_ids=selected_ids,
        selected_scores=selected_scores,
        pruned_output=pruned_output or "(empty)",
        token_usage=usage,
        selector_skill_matches=selector_skill_matches,
        tools_selector=tools_selector,
        skills_selector=skills_selector,
    )


def _print_selector_leg(title: str, leg: SelectorLegTrace) -> None:
    _print_section(f"{title} — bulk plan", _format_bulk_plan(leg.bulk_plan))
    _print_section(f"{title} — selector token metadata", leg.selector_token_metadata)
    _print_section(f"{title} — system prompt", leg.system_prompt)
    _print_section(f"{title} — user prompt", leg.user_prompt)
    _print_section(f"{title} — selected scores", _format_selector_selections(leg.selected_scores))
    _print_section(f"{title} — selected ids", json.dumps(sorted(leg.selected_ids)))
    _print_section(
        f"{title} — token usage",
        json.dumps(
            {
                "input_tokens": leg.token_usage.input_tokens,
                "output_tokens": leg.token_usage.output_tokens,
                "reasoning_tokens": leg.token_usage.reasoning_tokens,
                "model": leg.token_usage.model_name,
                "provider": leg.token_usage.provider,
            },
            indent=2,
        ),
    )
    _print_section(f"{title} — pruned output", leg.pruned_output)


def print_llm_prune_trace(trace: LlmPruneTrace) -> None:
    _print_section(f"selector scenario: {trace.mode} (agent={trace.agent})", "")
    _print_section(
        "raw hook payload (cyt-client stdin)",
        json.dumps(trace.raw_hook_payload, indent=2),
    )
    _print_section(
        "enriched hook payload (POST /hook/inject body)",
        json.dumps(trace.enriched_hook_payload, indent=2),
    )
    _print_section("enriched query", trace.query)

    if trace.mode == "combined" and trace.tools_selector and trace.skills_selector:
        _print_selector_leg("tools selector", trace.tools_selector)
        _print_selector_leg("skills selector", trace.skills_selector)
        _print_section(
            "combined token usage",
            json.dumps(
                {
                    "input_tokens": trace.token_usage.input_tokens,
                    "output_tokens": trace.token_usage.output_tokens,
                    "reasoning_tokens": trace.token_usage.reasoning_tokens,
                    "model": trace.token_usage.model_name,
                    "provider": trace.token_usage.provider,
                },
                indent=2,
            ),
        )
        _print_section("combined pruned output", trace.pruned_output)
        return

    if trace.tools_selector is not None:
        _print_selector_leg("tools selector", trace.tools_selector)
        return
    if trace.skills_selector is not None:
        _print_selector_leg("skills selector", trace.skills_selector)
        return

    _print_section("system prompt", trace.system_prompt)
    _print_section("user prompt", trace.user_prompt)
    _print_section("selected scores", _format_selector_selections(trace.selected_scores))
    _print_section("selected ids", json.dumps(sorted(trace.selected_ids)))
    _print_section(
        "token usage",
        json.dumps(
            {
                "input_tokens": trace.token_usage.input_tokens,
                "output_tokens": trace.token_usage.output_tokens,
                "reasoning_tokens": trace.token_usage.reasoning_tokens,
                "model": trace.token_usage.model_name,
                "provider": trace.token_usage.provider,
            },
            indent=2,
        ),
    )
    _print_section("pruned output", trace.pruned_output)


def print_hook_daemon_trace(trace: HookDaemonTrace) -> None:
    _print_section(f"hook daemon scenario: {trace.mode} (agent={trace.agent})", "")
    _print_section("hook module", cyt_hook_http_server.__name__)
    _print_section(
        "raw hook payload (cyt-client stdin)",
        json.dumps(trace.raw_hook_payload, indent=2),
    )
    _print_section(
        "enriched hook payload (POST /hook/inject body)",
        json.dumps(trace.enriched_hook_payload, indent=2),
    )
    _print_section("cyt_agent", str(trace.enriched_hook_payload.get("cyt_agent", "")))
    _print_section("outcome", trace.outcome)
    _print_section("stdout (hook inject response)", trace.stdout_text or "(empty)")
    if trace.rules_file is not None:
        _print_section("cursor rules file", str(trace.rules_file))
    if trace.details:
        _print_section("details", json.dumps(trace.details, indent=2, default=str))


def _fixtures_available() -> bool:
    return DEFAULT_TOOL_JSON.is_file() and DEFAULT_SKILL_ENTRY_DIR.is_dir()


def _llm_credentials_available(config: dict[str, Any]) -> bool:
    try:
        require_proxy_env(config)
    except RuntimeError:
        return False
    return True


def test_plan_selector_bulks_reports_budget_per_bulk() -> None:
    plan = _plan_selector_bulks(
        "query",
        SKILLS_SELECTOR_SYSTEM_PROMPT,
        ["skill-block-a", "skill-block-b"],
        chunk_token_counts=[1200, 1300],
        domain="skills",
        soft_budget_total=5000,
    )
    assert plan.domain == "skills"
    assert plan.soft_budget_total == 5000
    assert plan.num_bulks == len(plan.bulks)
    assert plan.multiple_batches == (plan.num_bulks > 1)
    if plan.num_bulks > 1:
        assert plan.per_bulk_soft_budget == max(100, 5000 // plan.num_bulks)
    else:
        assert plan.per_bulk_soft_budget == 5000
    assert sum(entry.item_count for entry in plan.bulks) == 2
    formatted = _format_bulk_plan(plan)
    assert "multiple_batches:" in formatted
    assert "per_bulk_soft_budget:" in formatted


@pytest.mark.parametrize("mode", ["tools", "skills", "combined"])
def test_llm_prune_integration(
    mode: ScenarioMode,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    if not _fixtures_available():
        pytest.skip("default ~/.config/cyt fixtures are not present on this machine")
    agent = _parse_agent(request.config.getoption("--agent"))
    rule_path = request.config.getoption("--rule")
    config = _integration_config(
        mode=mode,
        stats_db=str(tmp_path / "stats.db"),
    )
    if not _llm_credentials_available(config):
        pytest.skip("pruning LLM credentials are not configured")

    selector_trace = run_llm_prune_scenario(
        mode,
        prompt=DEFAULT_USER_PROMPT,
        config=config,
        agent=agent,
    )
    print_llm_prune_trace(selector_trace)
    assert isinstance(selector_trace.selected_ids, set)
    assert isinstance(selector_trace.selected_scores, dict)
    assert all(
        isinstance(chunk_id, int) and isinstance(score, int)
        for chunk_id, score in selector_trace.selected_scores.items()
    )
    if selector_trace.tools_selector is not None:
        assert selector_trace.tools_selector.bulk_plan.domain == "tools"
        assert (
            selector_trace.tools_selector.bulk_plan.soft_budget_total
            == SELECTOR_SOFT_BUDGET_TOOLS_TOTAL
        )
    if selector_trace.mode == "skills" and selector_trace.skills_selector is not None:
        assert selector_trace.selected_scores == selector_trace.skills_selector.selected_scores
        assert selector_trace.skills_selector.bulk_plan.domain == "skills"
        assert (
            selector_trace.skills_selector.bulk_plan.soft_budget_total
            == SELECTOR_SOFT_BUDGET_SKILLS_TOTAL
        )
    assert selector_trace.enriched_hook_payload.get("cyt_agent") == agent

    daemon_trace = run_hook_daemon_scenario(
        mode,
        prompt=DEFAULT_USER_PROMPT,
        config=config,
        agent=agent,
        rule_path=rule_path,
        selector_trace=selector_trace,
    )
    print_hook_daemon_trace(daemon_trace)
    assert daemon_trace.enriched_hook_payload.get("cyt_agent") == agent
    assert daemon_trace.outcome in {
        "user_prompt_injected",
        "user_prompt_tools_injected",
        "user_prompt_skills_injected",
    }
    assert daemon_trace.stdout_text.strip()
    if rule_path and agent == "cursor":
        assert daemon_trace.rules_file is not None
        assert daemon_trace.rules_file.is_file()


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=["tools", "skills", "combined", "all"],
        default="all",
        help="which scenario to run (default: all)",
    )
    parser.add_argument("--prompt", default=DEFAULT_USER_PROMPT, help="user hook prompt")
    parser.add_argument(
        "--agent",
        choices=["cursor", "claude", "codex"],
        default=DEFAULT_AGENT,
        help="agent harness to simulate (default: cursor)",
    )
    parser.add_argument(
        "--rule",
        metavar="PATH",
        default=None,
        help="write hook injection to Cursor rules file (requires --agent cursor)",
    )
    parser.add_argument("--tool-json", type=Path, default=DEFAULT_TOOL_JSON)
    parser.add_argument("--skill-entry", type=Path, default=DEFAULT_SKILL_ENTRY_DIR)
    parser.add_argument("--skill-node-id", type=int, default=DEFAULT_SKILL_NODE_ID)
    args = parser.parse_args()

    modes: list[ScenarioMode]
    if args.mode == "all":
        modes = ["tools", "skills", "combined"]
    else:
        modes = [args.mode]

    needs_tools = any(mode in {"tools", "combined"} for mode in modes)
    needs_skills = any(mode in {"skills", "combined"} for mode in modes)
    if needs_tools and not args.tool_json.is_file():
        print(f"tool fixture missing: {args.tool_json}", file=sys.stderr)
        return 1
    if needs_skills and not args.skill_entry.is_dir():
        print(f"skill fixture missing: {args.skill_entry}", file=sys.stderr)
        return 1

    agent = _parse_agent(args.agent)

    with tempfile.TemporaryDirectory() as tmp:
        for mode in modes:
            config = _integration_config(
                mode=mode,
                stats_db=str(Path(tmp) / f"{mode}-stats.db"),
            )
            try:
                require_proxy_env(config)
            except RuntimeError as exc:
                print(exc, file=sys.stderr)
                return 1

            selector_trace = run_llm_prune_scenario(
                mode,
                prompt=args.prompt,
                config=config,
                agent=agent,
                tool_json_path=args.tool_json,
                skill_entry_dir=args.skill_entry,
                skill_node_id=args.skill_node_id,
            )
            print_llm_prune_trace(selector_trace)

            daemon_trace = run_hook_daemon_scenario(
                mode,
                prompt=args.prompt,
                config=config,
                agent=agent,
                tool_json_path=args.tool_json,
                skill_entry_dir=args.skill_entry,
                skill_node_id=args.skill_node_id,
                rule_path=args.rule,
                selector_trace=selector_trace,
            )
            print_hook_daemon_trace(daemon_trace)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
