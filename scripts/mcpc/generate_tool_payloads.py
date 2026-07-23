#!/usr/bin/env python3
"""Generate minimum workable mcpc tool-call payloads per session/tool."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
DEFS_JSON = SCRIPT_DIR / "output" / "mcp-definitions.json"
PAYLOADS_DIR = SCRIPT_DIR / "payloads"
FIXTURES_DIR = PAYLOADS_DIR / "_fixtures"

HEDL_JSON = '{"users":[{"id":"1","name":"Alice"}]}'
HEDL_SAMPLE = (
    "%V:2.0\n"
    "%NULL:~\n"
    '%QUOTE:"\n'
    "%S:User:[id,name]\n"
    "%C:User.total=1\n"
    "---\n"
    "users:@User\n"
    ' |"1",Alice\n'
)

SMOKE_SKIP_TOOLS = {
    "jcodemunch/index_repo",  # hits GitHub rate limits
    "jcodemunch/invalidate_cache",  # destructive: wipes the index
    "jcodemunch/search_columns",  # requires dbt/sqlmesh column metadata
    "jcodemunch/get_group_contracts",  # requires 2+ indexed repos in one group
    "jcodemunch/embed_repo",  # heavy / long-running
    "codebase-memory/delete_project",  # destructive / always isError
    "codebase-memory/index_repository",  # re-indexes entire repo
    "context-mode/ctx_purge",  # destructive
    "gitnexus/rename",  # mutating
}

NO_ARG_TOOLS = {
    "list_repos",
    "get_watch_status",
    "get_session_stats",
    "analyze_perf",
    "check_embedding_drift",
    "tune_weights",
    "test_summarizer",
    "audit_agent_config",
    "suggest_corrections",
    "get_cross_repo_map",
    "jcodemunch_guide",
    "build_or_update_graph_tool",
    "run_postprocess_tool",
    "get_minimal_context_tool",
    "get_impact_radius_tool",
    "get_review_context_tool",
    "embed_graph_tool",
    "list_graph_stats_tool",
    "find_large_functions_tool",
    "list_flows_tool",
    "get_flow_tool",
    "get_affected_flows_tool",
    "list_communities_tool",
    "get_community_tool",
    "get_architecture_overview_tool",
    "detect_changes_tool",
    "refactor_tool",
    "generate_wiki_tool",
    "get_hub_nodes_tool",
    "get_bridge_nodes_tool",
    "get_knowledge_gaps_tool",
    "get_surprising_connections_tool",
    "get_suggested_questions_tool",
    "list_repos_tool",
    "god_nodes",
    "graph_stats",
    "list_prs",
    "triage_prs",
    "skill_colgrep",
    "skill_install",
    "ctx_stats",
    "ctx_doctor",
    "ctx_upgrade",
    "ctx_insight",
    "list_projects",
    "context",
    "check",
    "explain",
    "route_map",
    "tool_map",
    "shape_check",
    "api_impact",
    "group_list",
    "trace",
}


def mcpc_json(args: list[str], payload: dict[str, Any] | None = None) -> Any:
    cmd = ["mcpc", "--json", *args]
    proc = subprocess.run(
        cmd,
        input=(json.dumps(payload) + "\n") if payload is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def parse_mcp_text(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    structured = data.get("structuredContent")
    if structured is not None:
        return structured
    content = data.get("content")
    if isinstance(content, list) and content:
        text = content[0].get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


def bootstrap_hedl_sample() -> str:
    data = mcpc_json(
        ["@hedl", "tools-call", "hedl_convert_from"],
        {"content": HEDL_JSON, "format": "json"},
    )
    parsed = parse_mcp_text(data)
    if isinstance(parsed, dict) and isinstance(parsed.get("hedl"), str):
        return parsed["hedl"]
    return HEDL_SAMPLE


def write_fixtures(ctx: dict[str, Any]) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / "sample.hedl").write_text(ctx["hedl"], encoding="utf-8")
    otel = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "name": "smoke",
                                "attributes": [
                                    {"key": "code.filepath", "value": {"stringValue": ctx["file_path"]}},
                                    {"key": "code.lineno", "value": {"intValue": ctx["line"]}},
                                    {"key": "code.function", "value": {"stringValue": ctx["symbol"]}},
                                ],
                            },
                        ],
                    },
                ],
            },
        ],
    }
    otel_path = FIXTURES_DIR / "sample-otel.json"
    otel_path.write_text(json.dumps(otel), encoding="utf-8")
    ctx["otel_path"] = str(otel_path)


def resolve_context(cwd: Path) -> dict[str, Any]:
    hedl = bootstrap_hedl_sample()
    ctx: dict[str, Any] = {
        "cwd": str(cwd),
        "repo_path": str(cwd),
        "file_path": "src/cyt/config/__init__.py",
        "module_path": "src/cyt/config",
        "file": "src/cyt/config/__init__.py",
        "line": 41,
        "symbol": "load_config",
        "symbol_id": "src/cyt/mcpc/runtime.py::load_config#function",
        "class_name": "RuntimeContext",
        "identifier": "load_config",
        "target": "load_config",
        "query": "config loading",
        "pattern": "load_config",
        "question": "How does configuration loading work?",
        "search_query": "load_config",
        "library_name": "clear-your-tools",
        "library_id": "/qdrddr/clear-your-tools",
        "repo_name": "qdrddr/clear-your-tools",
        "gitnexus_repo": "clear-your-tools",
        "refactor_type": "extract",
        "new_name": "load_app_config",
        "section_name": "overview",
        "community_name": "config",
        "refactor_id": "smoke-test",
        "source": "digraph { a -> b }",
        "format": "json",
        "hedl": hedl,
        "hedl_json": HEDL_JSON,
        "hedl_path": str(FIXTURES_DIR / "sample.hedl"),
        "repo_a": "qdrddr/clear-your-tools",
        "repo_b": "qdrddr/clear-your-tools",
        "baseline": {"composite": 70, "axes": {"complexity": 70, "coverage": 70}},
        "current": {"composite": 72, "axes": {"complexity": 68, "coverage": 75}},
        "criteria": "public functions",
        "tier": "standard",
        "model": "test-model",
        "task": "understand config loading",
        "package": "pytest",
        "slug": "colgrep",
        "skill_path": "SKILL.md",
        "pr_number": 1,
        "source_repo": "qdrddr/clear-your-tools",
        "target_repo": "qdrddr/clear-your-tools",
        "repos": ["qdrddr/clear-your-tools"],
        "community_id": 1,
        "label": "Function",
        "source_label": "load_config",
        "target_label": "load_config",
        "mode": "guards",
        "direction": "downstream",
        "statement": "MATCH (n) RETURN n LIMIT 1",
        "traces": [{"name": "smoke", "spans": []}],
        "operations": [
            {
                "id": "validate-1",
                "tool": "hedl_validate",
                "arguments": {"hedl": hedl, "strict": False, "lint": False},
            },
        ],
        "commands": [{"label": "pwd", "command": "pwd"}],
        "queries": ["working directory"],
        "requests": [{"url": "https://example.com"}],
        "code": "console.log('ok')",
        "language": "javascript",
        "content": hedl,
        "json": HEDL_JSON,
        "project": "",
        "repo": "",
        "qualified_name": "",
        "function_name": "",
        "otel_path": "",
        "diagram_source": {"root": "", "nodes": []},
    }

    mcpc_json(
        ["@jcodemunch", "tools-call", "index_folder"],
        {"path": ctx["cwd"], "incremental": True},
    )

    resolve = mcpc_json(["@jcodemunch", "tools-call", "resolve_repo"], {"path": ctx["cwd"]})
    parsed = parse_mcp_text(resolve)
    if isinstance(parsed, dict) and parsed.get("repo"):
        ctx["repo"] = parsed["repo"]
        ctx["repo_a"] = parsed["repo"]
        ctx["repo_b"] = parsed["repo"]
        ctx["source_repo"] = parsed["repo"]
        ctx["target_repo"] = parsed["repo"]
        ctx["repos"] = [parsed["repo"]]

        search = mcpc_json(
            ["@jcodemunch", "tools-call", "search_symbols"],
            {"repo": ctx["repo"], "query": ctx["symbol"], "file_pattern": "src/**", "max_results": 1},
        )
        search_parsed = parse_mcp_text(search)
        if isinstance(search_parsed, dict):
            results = search_parsed.get("results")
            if isinstance(results, list) and results:
                first = results[0]
                if isinstance(first, dict) and first.get("id"):
                    ctx["symbol_id"] = first["id"]

        health = mcpc_json(["@jcodemunch", "tools-call", "get_repo_health"], {"repo": ctx["repo"]})
        health_parsed = parse_mcp_text(health)
        if isinstance(health_parsed, dict) and isinstance(health_parsed.get("radar"), dict):
            radar = health_parsed["radar"]
            ctx["baseline"] = radar
            ctx["current"] = radar

        hierarchy = mcpc_json(
            ["@jcodemunch", "tools-call", "get_call_hierarchy"],
            {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"], "depth": 1},
        )
        hierarchy_parsed = parse_mcp_text(hierarchy)
        if isinstance(hierarchy_parsed, dict):
            ctx["diagram_source"] = hierarchy_parsed

    projects = mcpc_json(["@codebase-memory", "tools-call", "list_projects"], {})
    parsed = parse_mcp_text(projects)
    projects_list = parsed.get("projects") if isinstance(parsed, dict) else None
    if isinstance(projects_list, list):
        cwd_s = ctx["cwd"]
        best = None
        for item in projects_list:
            root = item.get("root_path") or item.get("root") or ""
            if cwd_s == root or cwd_s.startswith(root.rstrip("/") + "/"):
                if best is None or len(root) > len(best.get("root_path", best.get("root", ""))):
                    best = item
        if best:
            ctx["project"] = best.get("name") or best.get("project") or ""
            qn = f"{ctx['project']}.src.cyt.config.load_config"
            ctx["qualified_name"] = qn
            ctx["function_name"] = qn

    gitnexus = mcpc_json(["@gitnexus", "tools-call", "list_repos"], {})
    parsed = parse_mcp_text(gitnexus)
    repos = parsed.get("repositories") if isinstance(parsed, dict) else None
    if isinstance(repos, list):
        names = [r.get("name") for r in repos if isinstance(r, dict)]
        for preferred in ("clear-your-tools", "chunk-your-tools"):
            if preferred in names:
                ctx["gitnexus_repo"] = preferred
                break

    write_fixtures(ctx)
    return ctx


def empty(_: dict[str, Any]) -> dict[str, Any]:
    return {}


def payload_for(session: str, tool: str, ctx: dict[str, Any]) -> dict[str, Any]:
    s = session.lstrip("@")
    key = f"{s}/{tool}"

    shared: dict[str, dict[str, Any]] = {
        "hedl/hedl_read": {"path": ctx["hedl_path"]},
        "hedl/hedl_query": {"hedl": ctx["hedl"]},
        "hedl/hedl_validate": {"hedl": ctx["hedl"], "strict": False, "lint": False},
        "hedl/hedl_optimize": {"json": ctx["hedl_json"]},
        "hedl/hedl_stats": {"hedl": ctx["hedl"]},
        "hedl/hedl_format": {"hedl": ctx["hedl"]},
        "hedl/hedl_write": {"path": "sample.hedl", "content": ctx["hedl"]},
        "hedl/hedl_convert_to": {"hedl": ctx["hedl"], "format": "json"},
        "hedl/hedl_convert_from": {"content": ctx["hedl_json"], "format": "json"},
        "hedl/hedl_stream": {"hedl": ctx["hedl"]},
        "hedl/batch": {"operations": ctx["operations"]},
        "semble/search": {"query": ctx["query"], "repo": ctx["repo"]},
        "semble/find_related": {
            "query": ctx["query"],
            "repo": ctx["repo"],
            "file_path": ctx["file_path"],
            "line": ctx["line"],
        },
        "fff/find_files": {"query": "run-mcp-tools"},
        "fff/grep": {"query": "run_mcpc_call"},
        "fff/multi_grep": {"patterns": ["run_mcpc_call"], "constraints": "scripts/mcpc/**", "context": 2},
        "jcodemunch/index_repo": {"url": "qdrddr/clear-your-tools", "incremental": True},
        "jcodemunch/index_folder": {"path": ctx["cwd"], "incremental": True},
        "jcodemunch/summarize_repo": {"repo": ctx["repo"], "force": False},
        "jcodemunch/index_file": {"path": ctx["file_path"]},
        "jcodemunch/index_dependency": {"repo": ctx["repo"], "package": ctx["package"]},
        "jcodemunch/import_runtime_signal": {
            "path": ctx["otel_path"],
            "repo": ctx["repo"],
            "source": "otel",
        },
        "jcodemunch/get_runtime_coverage": {"repo": ctx["repo"]},
        "jcodemunch/find_hot_paths": {"repo": ctx["repo"]},
        "jcodemunch/find_unused_paths": {"repo": ctx["repo"]},
        "jcodemunch/get_redaction_log": {"repo": ctx["repo"]},
        "jcodemunch/resolve_repo": {"path": ctx["cwd"]},
        "jcodemunch/get_file_tree": {"repo": ctx["repo"], "path": "src/cyt"},
        "jcodemunch/get_file_outline": {"repo": ctx["repo"], "file_path": ctx["file_path"]},
        "jcodemunch/get_symbol_source": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_file_content": {"repo": ctx["repo"], "file_path": ctx["file_path"], "start_line": 1, "end_line": 20},
        "jcodemunch/search_symbols": {"repo": ctx["repo"], "query": ctx["symbol"], "file_pattern": "src/**", "max_results": 3},
        "jcodemunch/invalidate_cache": {"repo": ctx["repo"]},
        "jcodemunch/search_text": {"repo": ctx["repo"], "query": "load_config", "file_pattern": "src/cyt/**", "max_results": 3},
        "jcodemunch/get_repo_outline": {"repo": ctx["repo"]},
        "jcodemunch/find_importers": {"repo": ctx["repo"], "file_path": ctx["file_path"], "max_results": 5},
        "jcodemunch/find_references": {"repo": ctx["repo"], "identifier": ctx["identifier"], "max_results": 5},
        "jcodemunch/check_references": {
            "repo": ctx["repo"], "identifier": ctx["identifier"],
            "search_content": True, "max_content_results": 3,
        },
        "jcodemunch/search_columns": {"repo": ctx["repo"], "query": "config", "max_results": 3},
        "jcodemunch/get_context_bundle": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_file_risk": {"repo": ctx["repo"], "file_path": ctx["file_path"]},
        "jcodemunch/diff_health_radar": {"baseline": ctx["baseline"], "current": ctx["current"]},
        "jcodemunch/digest": {"repo": ctx["repo"]},
        "jcodemunch/plan_turn": {"repo": ctx["repo"], "query": ctx["query"]},
        "jcodemunch/register_edit": {"repo": ctx["repo"], "file_paths": [ctx["file_path"]]},
        "jcodemunch/get_dependency_graph": {"repo": ctx["repo"], "file": ctx["file_path"]},
        "jcodemunch/get_symbol_diff": {"repo_a": ctx["repo_a"], "repo_b": ctx["repo_b"]},
        "jcodemunch/get_class_hierarchy": {"repo": ctx["repo"], "class_name": ctx["class_name"]},
        "jcodemunch/get_related_symbols": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/suggest_queries": {"repo": ctx["repo"]},
        "jcodemunch/get_blast_radius": {"repo": ctx["repo"], "symbol": ctx["symbol_id"]},
        "jcodemunch/get_call_hierarchy": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_impact_preview": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_symbol_provenance": {"repo": ctx["repo"], "symbol": ctx["symbol"]},
        "jcodemunch/get_pr_risk_profile": {"repo": ctx["repo"]},
        "jcodemunch/get_dependency_cycles": {"repo": ctx["repo"]},
        "jcodemunch/get_coupling_metrics": {"repo": ctx["repo"], "module_path": ctx["module_path"]},
        "jcodemunch/get_layer_violations": {"repo": ctx["repo"]},
        "jcodemunch/check_rename_safe": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"], "new_name": ctx["new_name"]},
        "jcodemunch/check_delete_safe": {"repo": ctx["repo"], "symbol": ctx["symbol"]},
        "jcodemunch/check_edit_safe": {"repo": ctx["repo"], "symbol": ctx["symbol"]},
        "jcodemunch/find_implementations": {"repo": ctx["repo"], "symbol": ctx["symbol"]},
        "jcodemunch/plan_refactoring": {"repo": ctx["repo"], "symbol": ctx["symbol"], "refactor_type": ctx["refactor_type"]},
        "jcodemunch/get_dead_code_v2": {"repo": ctx["repo"]},
        "jcodemunch/get_extraction_candidates": {"repo": ctx["repo"], "file_path": ctx["file_path"]},
        "jcodemunch/get_symbol_complexity": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_churn_rate": {"repo": ctx["repo"], "target": ctx["file_path"]},
        "jcodemunch/get_delivery_metrics": {"repo": ctx["repo"], "window_days": 30},
        "jcodemunch/get_parity_map": {
            "source_repo": ctx["source_repo"],
            "target_repo": ctx["target_repo"],
            "source_path": "src/cyt",
            "target_path": "src/cyt",
        },
        "jcodemunch/get_decorator_census": {"repo": ctx["repo"]},
        "jcodemunch/get_architecture_metrics": {"repo": ctx["repo"]},
        "jcodemunch/get_hotspots": {"repo": ctx["repo"]},
        "jcodemunch/get_repo_health": {"repo": ctx["repo"]},
        "jcodemunch/get_untested_symbols": {"repo": ctx["repo"]},
        "jcodemunch/search_ast": {"repo": ctx["repo"], "category": "maintenance", "max_results": 5},
        "jcodemunch/get_symbol_importance": {"repo": ctx["repo"]},
        "jcodemunch/find_similar_symbols": {"repo": ctx["repo"], "symbol_id": ctx["symbol_id"]},
        "jcodemunch/get_repo_map": {"repo": ctx["repo"]},
        "jcodemunch/find_dead_code": {"repo": ctx["repo"]},
        "jcodemunch/get_ranked_context": {"repo": ctx["repo"], "query": ctx["query"], "token_budget": 2000, "scope": "src/cyt/**"},
        "jcodemunch/assemble_task_context": {"repo": ctx["repo"], "task": ctx["task"]},
        "jcodemunch/get_changed_symbols": {"repo": ctx["repo"]},
        "jcodemunch/embed_repo": {"repo": ctx["repo"]},
        "jcodemunch/get_group_contracts": {"repos": ctx["repos"]},
        "jcodemunch/get_tectonic_map": {"repo": ctx["repo"]},
        "jcodemunch/get_signal_chains": {"repo": ctx["repo"]},
        "jcodemunch/get_endpoint_impact": {"repo": ctx["repo"], "handler_symbol_id": ctx["symbol_id"]},
        "jcodemunch/render_diagram": {"source": ctx["diagram_source"]},
        "jcodemunch/get_project_intel": {"repo": ctx["repo"]},
        "jcodemunch/list_workspaces": {"repo": ctx["repo"]},
        "jcodemunch/winnow_symbols": {
            "repo": ctx["repo"],
            "criteria": [{"axis": "kind", "op": "eq", "value": "function"}],
            "max_results": 5,
        },
        "jcodemunch/set_tool_tier": {"tier": ctx["tier"]},
        "jcodemunch/announce_model": {"model": ctx["model"]},
        "codebase-memory/index_repository": {"repo_path": ctx["repo_path"]},
        "codebase-memory/search_graph": {"project": ctx["project"], "query": ctx["query"], "limit": 5},
        "codebase-memory/query_graph": {
            "project": ctx["project"],
            "query": "MATCH (n:Function) RETURN n.name LIMIT 3",
        },
        "codebase-memory/trace_path": {"project": ctx["project"], "function_name": ctx["function_name"]},
        "codebase-memory/get_code_snippet": {"project": ctx["project"], "qualified_name": ctx["qualified_name"]},
        "codebase-memory/get_graph_schema": {"project": ctx["project"]},
        "codebase-memory/get_architecture": {"project": ctx["project"]},
        "codebase-memory/search_code": {"project": ctx["project"], "pattern": ctx["pattern"], "limit": 5},
        "codebase-memory/index_status": {"project": ctx["project"]},
        "codebase-memory/detect_changes": {"project": ctx["project"]},
        "codebase-memory/manage_adr": {"project": ctx["project"], "action": "list"},
        "codebase-memory/ingest_traces": {"project": ctx["project"], "traces": ctx["traces"]},
        "codebase-memory/delete_project": {"project": "__nonexistent_smoke_project__"},
        "gitnexus/query": {"search_query": ctx["search_query"], "repo": ctx["gitnexus_repo"], "limit": 3},
        "gitnexus/cypher": {"statement": ctx["statement"], "repo": ctx["gitnexus_repo"]},
        "gitnexus/rename": {"new_name": ctx["new_name"], "symbol": ctx["symbol"], "repo": ctx["gitnexus_repo"]},
        "gitnexus/impact": {"target": ctx["symbol"], "direction": ctx["direction"], "repo": ctx["gitnexus_repo"]},
        "gitnexus/pdg_query": {"mode": ctx["mode"], "target": ctx["symbol"], "repo": ctx["gitnexus_repo"]},
        "gitnexus/group_sync": {"name": ctx["gitnexus_repo"]},
        "gitnexus/detect_changes": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/context": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/check": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/explain": {"repo": ctx["gitnexus_repo"], "target": ctx["symbol"]},
        "gitnexus/route_map": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/tool_map": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/shape_check": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/api_impact": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/group_list": {"repo": ctx["gitnexus_repo"]},
        "gitnexus/trace": {"repo": ctx["gitnexus_repo"], "target": ctx["symbol"]},
        "code-review-graph/query_graph_tool": {"pattern": "load_config", "target": ctx["file_path"]},
        "code-review-graph/semantic_search_nodes_tool": {"query": ctx["query"]},
        "code-review-graph/get_docs_section_tool": {"section_name": ctx["section_name"]},
        "code-review-graph/apply_refactor_tool": {"refactor_id": ctx["refactor_id"]},
        "code-review-graph/get_wiki_page_tool": {"community_name": ctx["community_name"]},
        "code-review-graph/traverse_graph_tool": {"query": ctx["query"]},
        "code-review-graph/cross_repo_search_tool": {"query": ctx["query"]},
        "codegraph/codegraph_explore": {"query": ctx["query"]},
        "graphify/query_graph": {"question": ctx["question"]},
        "graphify/get_node": {"label": ctx["label"]},
        "graphify/get_neighbors": {"label": ctx["label"]},
        "graphify/get_community": {"community_id": ctx["community_id"]},
        "graphify/shortest_path": {"source": ctx["source_label"], "target": ctx["target_label"]},
        "graphify/get_pr_impact": {"pr_number": ctx["pr_number"]},
        "coolgrep-skill/read_skill_file": {"slug": ctx["slug"], "path": ctx["skill_path"]},
        "context7/resolve-library-id": {"query": ctx["query"], "libraryName": ctx["library_name"]},
        "context7/query-docs": {"libraryId": ctx["library_id"], "query": ctx["query"]},
        "deepwiki/ask_question": {"repoName": ctx["repo_name"], "question": ctx["question"]},
        "deepwiki/read_wiki_contents": {"repoName": ctx["repo_name"]},
        "deepwiki/read_wiki_structure": {"repoName": ctx["repo_name"]},
        "context-mode/ctx_execute": {"language": ctx["language"], "code": ctx["code"]},
        "context-mode/ctx_execute_file": {
            "path": ctx["file_path"],
            "language": ctx["language"],
            "code": "console.log(FILE_CONTENT.split('\\n').length)",
        },
        "context-mode/ctx_index": {"content": "# Smoke\n\nTest section\n", "source": "smoke-test"},
        "context-mode/ctx_search": {"queries": ctx["queries"]},
        "context-mode/ctx_fetch_and_index": {"requests": ctx["requests"]},
        "context-mode/ctx_batch_execute": {"commands": ctx["commands"], "queries": ctx["queries"]},
    }

    if key in shared:
        payload = shared[key]
    elif tool in NO_ARG_TOOLS:
        payload = {}
    elif tool == "detect_changes" and s == "gitnexus":
        payload = {"repo": ctx["gitnexus_repo"]}
    else:
        payload = {}

    if key in SMOKE_SKIP_TOOLS:
        payload = dict(payload)
        payload["_smoke_skip"] = True
        payload["_smoke_skip_reason"] = "destructive or environment-dependent"
    return payload


def list_session_tools(session: str) -> list[str]:
    data = mcpc_json([session, "tools-list"])
    if isinstance(data, list):
        return [item["name"] for item in data if isinstance(item, dict) and item.get("name")]
    return []


def session_tool_schemas(session: str) -> dict[str, dict[str, Any]]:
    data = mcpc_json([session, "tools-list"])
    schemas: dict[str, dict[str, Any]] = {}
    if not isinstance(data, list):
        return schemas
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        schema = item.get("inputSchema") or item.get("input_schema")
        if name and isinstance(schema, dict):
            schemas[str(name)] = schema
    return schemas


def scalar_type(spec: dict[str, Any]) -> str:
    raw_type = spec.get("type")
    if isinstance(raw_type, list):
        for item in raw_type:
            if item != "null":
                return str(item)
        return "string"
    if isinstance(raw_type, str):
        return raw_type
    if spec.get("enum"):
        return "enum"
    if isinstance(spec.get("properties"), dict):
        return "object"
    if spec.get("items") is not None:
        return "array"
    return "string"


def ctx_lookup(ctx: dict[str, Any], name: str) -> Any:
    aliases = {
        "repoName": "repo_name",
        "libraryName": "library_name",
        "libraryId": "library_id",
        "function_name": "function_name",
        "qualified_name": "qualified_name",
        "search_query": "search_query",
        "symbol_id": "symbol_id",
        "file_path": "file_path",
        "module_path": "module_path",
        "class_name": "class_name",
        "refactor_id": "refactor_id",
        "section_name": "section_name",
        "community_name": "community_name",
        "community_id": "community_id",
        "handler_symbol_id": "symbol_id",
        "new_name": "new_name",
        "source_repo": "source_repo",
        "target_repo": "target_repo",
        "source_label": "source_label",
        "target_label": "target_label",
        "pr_number": "pr_number",
        "gitnexus_repo": "gitnexus_repo",
    }
    if name in ctx:
        return ctx[name]
    if name in aliases:
        return ctx.get(aliases[name])
    snake = "".join(ch if ch.islower() else f"_{ch.lower()}" for ch in name).lstrip("_")
    if snake in ctx:
        return ctx[snake]
    return None


def sample_value(name: str, spec: dict[str, Any], ctx: dict[str, Any]) -> Any:
    if "default" in spec:
        return spec["default"]

    looked_up = ctx_lookup(ctx, name)
    if looked_up not in (None, ""):
        return looked_up

    value_type = scalar_type(spec)
    if value_type == "boolean":
        return False
    if value_type in {"number", "integer"}:
        return 1
    if value_type == "array":
        item_spec = spec.get("items") if isinstance(spec.get("items"), dict) else {}
        if name == "patterns":
            return ["test"]
        if name in {"paths", "file_paths", "symbol_ids", "repos", "identifiers", "queries", "commands", "requests", "operations", "traces"}:
            if name == "queries":
                return ctx.get("queries") or ["test"]
            if name == "commands":
                return ctx.get("commands") or [{"label": "smoke", "command": "pwd"}]
            if name == "requests":
                return ctx.get("requests") or [{"url": "https://example.com"}]
            if name == "repos":
                return ctx.get("repos") or ([ctx["repo"]] if ctx.get("repo") else [])
            if name == "file_paths":
                return [ctx.get("file_path") or "src/cyt/config/__init__.py"]
            if name == "operations":
                return ctx.get("operations") or []
            return []
        return [sample_value(f"{name}_item", item_spec, ctx)] if item_spec else []
    if value_type == "object":
        if name == "source" and ctx.get("diagram_source"):
            return ctx["diagram_source"]
        if name in {"baseline", "current"} and ctx.get(name):
            return ctx[name]
        return {}
    if value_type == "enum":
        enum = spec.get("enum") or []
        return enum[0] if enum else "test"

    if name == "path":
        return ctx.get("file_path") or ctx.get("cwd") or "."
    if name in {"query", "question", "prompt", "text", "search_query"}:
        return ctx.get("query") or ctx.get("question") or "test"
    if name in {"hedl", "content", "json"}:
        return ctx.get(name) or ctx.get("hedl") or "test"
    if name == "repo":
        return ctx.get("repo") or ctx.get("gitnexus_repo") or "test/repo"
    if name == "project":
        return ctx.get("project") or "test-project"
    if name == "url":
        return "qdrddr/clear-your-tools"
    if name == "statement":
        return ctx.get("statement") or "MATCH (n) RETURN n LIMIT 1"
    if name == "slug":
        return ctx.get("slug") or "colgrep"
    return "test"


def ensure_required_fields(
    payload: dict[str, Any],
    schema: dict[str, Any] | None,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    if not schema:
        return payload
    out = dict(payload)
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name in schema.get("required") or []:
        if name in out and out[name] not in (None, ""):
            continue
        spec = properties.get(name) if isinstance(properties.get(name), dict) else {}
        out[name] = sample_value(name, spec, ctx)
    return out


def missing_required_fields(payload: dict[str, Any], schema: dict[str, Any] | None) -> list[str]:
    if not schema:
        return []
    missing = []
    for name in schema.get("required") or []:
        if name not in payload or payload[name] in (None, ""):
            missing.append(name)
    return missing


def tools_from_definitions() -> dict[str, list[str]]:
    if not DEFS_JSON.is_file():
        return {}
    data = json.loads(DEFS_JSON.read_text(encoding="utf-8"))
    tools = [t.get("name") for t in data.get("tools", []) if isinstance(t, dict)]
    mapping: dict[str, list[str]] = {
        "hedl": [],
        "semble": [],
        "jcodemunch": [],
        "codebase-memory": [],
        "gitnexus": [],
        "fff": [],
        "code-review-graph": [],
        "context-mode": [],
        "codegraph": [],
        "graphify": [],
        "coolgrep-skill": [],
        "context7": [],
        "deepwiki": [],
    }
    prefixes = {
        "hedl_": "hedl",
        "ctx_": "context-mode",
        "skill_": "coolgrep-skill",
    }
    for name in tools:
        if not name:
            continue
        if name.startswith("hedl_") or name == "batch":
            mapping["hedl"].append(name)
        elif name in {"search", "find_related"}:
            mapping["semble"].append(name)
        elif name in mapping["fff"] or name in {"find_files", "grep", "multi_grep"}:
            if name in {"find_files", "grep", "multi_grep"}:
                mapping["fff"].append(name)
        elif name.endswith("_tool"):
            mapping["code-review-graph"].append(name)
        elif name == "codegraph_explore":
            mapping["codegraph"].append(name)
        elif name in {"resolve-library-id", "query-docs"}:
            mapping["context7"].append(name)
        elif name.startswith("read_wiki") or name == "ask_question":
            mapping["deepwiki"].append(name)
        else:
            for prefix, session in prefixes.items():
                if name.startswith(prefix):
                    mapping[session].append(name)
                    break
            else:
                for session in ("jcodemunch", "codebase-memory", "gitnexus", "graphify"):
                    mapping[session].append(name)
    return mapping


INDEX_INIT_PAYLOADS: dict[str, dict[str, Any]] = {
    "jcodemunch/index_folder": {
        "path": "{{repo_root}}",
        "incremental": False,
        "use_ai_summaries": True,
    },
    "jcodemunch/summarize_repo": {
        "repo": "{{repo}}",
        "force": False,
    },
    "codebase-memory/index_repository": {
        "repo_path": "{{repo_root}}",
        "mode": "full",
        "persistence": True,
    },
    "code-review-graph/build_or_update_graph_tool": {
        "full_rebuild": True,
        "repo_root": "{{repo_root}}",
    },
}

INDEX_UPDATE_PAYLOADS: dict[str, dict[str, Any]] = {
    "jcodemunch/index_folder": {
        "path": "{{repo_root}}",
        "incremental": True,
    },
    "jcodemunch/register_edit": {
        "repo": "{{repo}}",
        "file_paths": ["{{changed_files}}"],
        "reindex": True,
    },
    "codebase-memory/index_repository": {
        "repo_path": "{{repo_root}}",
        "mode": "fast",
    },
    "code-review-graph/build_or_update_graph_tool": {
        "repo_root": "{{repo_root}}",
    },
    "code-review-graph/detect_changes_tool": {
        "base": "HEAD~1",
        "repo_root": "{{repo_root}}",
    },
    "gitnexus/detect_changes": {
        "repo": "{{gitnexus_repo}}",
        "scope": "compare",
        "base_ref": "HEAD~1",
    },
}


def write_index_variant_payloads(
    variant: str,
    payloads: dict[str, dict[str, Any]],
    schemas_by_session: dict[str, dict[str, dict[str, Any]]],
    ctx: dict[str, Any],
) -> tuple[int, list[str]]:
    out_root = PAYLOADS_DIR / variant
    written = 0
    errors: list[str] = []

    for key, body in payloads.items():
        session_name, tool = key.split("/", 1)
        session = f"@{session_name}"
        schema = schemas_by_session.get(session, {}).get(tool)
        payload = ensure_required_fields(dict(body), schema, ctx)
        missing = missing_required_fields(payload, schema)
        if missing:
            errors.append(f"{variant}/{key}: missing {', '.join(missing)}")

        out_dir = out_root / session_name
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{tool}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1

    return written, errors


def main() -> int:
    cwd = Path.cwd().resolve()
    ctx = resolve_context(cwd)

    sessions_raw = mcpc_json([])
    sessions: list[str] = []
    if isinstance(sessions_raw, dict):
        sessions = [s["name"] for s in sessions_raw.get("sessions", []) if isinstance(s, dict) and s.get("name")]

    fallback = tools_from_definitions()
    written = 0
    validation_errors: list[str] = []
    schemas_by_session: dict[str, dict[str, dict[str, Any]]] = {}

    for session in sessions:
        tools = list_session_tools(session)
        schemas = session_tool_schemas(session)
        schemas_by_session[session] = schemas
        if not tools:
            tools = fallback.get(session.lstrip("@"), [])
        if not tools:
            print(f"warning: no tools for {session}", file=sys.stderr)
            continue

        out_dir = PAYLOADS_DIR / session.lstrip("@")
        out_dir.mkdir(parents=True, exist_ok=True)

        for tool in tools:
            payload = payload_for(session, tool, ctx)
            schema = schemas.get(tool)
            payload = ensure_required_fields(payload, schema, ctx)
            missing = missing_required_fields(payload, schema)
            if missing and not payload.get("_smoke_skip"):
                validation_errors.append(f"{session}/{tool}: missing {', '.join(missing)}")
            path = out_dir / f"{tool}.json"
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written += 1

    init_written, init_errors = write_index_variant_payloads(
        "index-init", INDEX_INIT_PAYLOADS, schemas_by_session, ctx,
    )
    update_written, update_errors = write_index_variant_payloads(
        "index-update", INDEX_UPDATE_PAYLOADS, schemas_by_session, ctx,
    )
    written += init_written + update_written
    validation_errors.extend(init_errors)
    validation_errors.extend(update_errors)

    print(f"Wrote {written} payload files to {PAYLOADS_DIR}")
    print(f"context repo={ctx['repo']!r} project={ctx['project']!r}")
    if validation_errors:
        print(f"warning: {len(validation_errors)} payload(s) still missing required fields:", file=sys.stderr)
        for line in validation_errors[:20]:
            print(f"  {line}", file=sys.stderr)
        if len(validation_errors) > 20:
            print("  ...", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
