#!/usr/bin/env -S uv run
"""Temporary BM25 smoke test — score tool descriptions against a query.

Usage:
  uv run src/tests/qa/bm25_scratch.py "read files from disk"
  uv run src/tests/qa/bm25_scratch.py   # uses DEFAULT_QUERY below
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path
from typing import Any

from cyt.pruners.bm25 import BM25_SCORE, bm25_catalog_dict
from cyt.pruners.documents import extract_json_catalog_document

# Edit these when experimenting.
DEFAULT_QUERY = "read files from disk"

_MCP_CONTEXT7_DESC = (
    "Use this server to fetch current documentation whenever the user asks about a library, framework, SDK, API, CLI tool, "
    "or cloud service -- even well-known ones like React, Next.js, Prisma, Express, Tailwind, Django, or Spring Boot. This "
    "includes API syntax, configuration, version migration, library-specific debugging, setup instructions, and CLI tool "
    "usage. Use even when you think you know the answer -- your training data may not reflect recent changes. Prefer this "
    "over web search for library docs.\n\nDo not use for: refactoring, writing scripts from scratch, debugging business logic, "
    "code review, or general programming concepts."
)

_MCP_CONTEXT7_LIBRARYID_DESC = (
    "Exact Context7-compatible library ID (e.g., '/mongodb/docs', '/vercel/next.js', '/supabase/supabase', "
    "'/vercel/next.js/v14.3.0-canary.87') retrieved from 'resolve-library-id' or directly from user query in the format "
    "'/org/project' or '/org/project/version'."
)

_MCP_CONTEXT7_RESOLVE_LIBRARY_ID_DESC = (
    "Resolves a package/product name to a Context7-compatible library ID and returns matching libraries.\n\nYou MUST call this "
    "function before 'Query Documentation' tool to obtain a valid Context7-compatible library ID UNLESS the user explicitly "
    "provides a library ID in the format '/org/project' or '/org/project/version' in their query.\n\nEach result includes:\n- "
    "Library ID: Context7-compatible identifier (format: /org/project)\n- Name: Library or package name\n- Description: Short "
    "summary\n- Code Snippets: Number of available code examples\n- Source Reputation: Authority indicator (High, Medium, Low, "
    "or Unknown)\n- Benchmark Score: Quality indicator (100 is the highest score)\n- Versions: List of versions if available. "
    "Use one of those versions if the user provides a version in their query. The format of the version is "
    "/org/project/version.\n\nFor best results, select libraries based on name match, source reputation, snippet coverage, "
    "benchmark score, and relevance to your use case.\n\nSelection Process:\n1. Analyze the query to understand what "
    "library/package the user is looking for\n2. Return the most relevant match based on:\n- Name similarity to the query "
    "(exact matches prioritized)\n- Description relevance to the query's intent\n- Documentation coverage (prioritize "
    "libraries with higher Code Snippet counts)\n- Source reputation (consider libraries with High or Medium reputation more "
    "authoritative)\n- Benchmark Score: Quality indicator (100 is the highest score)\n\nResponse Format:\n- Return the selected "
    "library ID in a clearly marked section\n- Provide a brief explanation for why this library was chosen\n- If multiple good "
    "matches exist, acknowledge this but proceed with the most relevant one\n- If no good matches exist, clearly state this "
    "and suggest query refinements\n\nFor ambiguous queries, request clarification before proceeding with a best-guess "
    "match.\n\nIMPORTANT: Do not call this tool more than 3 times per question. If you cannot find what you need after 3 "
    "calls, use the best result you have."
)

TOOL_DESCRIPTIONS: list[tuple[str, str]] = [
    ("read_file", "Read files from disk path and return their contents."),
    ("write_file", "Write or overwrite a file at a storage location on disk."),
    ("web_search", "Search the public web for recent news and documentation."),
    ("run_shell", "Execute a shell command in the project workspace."),
    ("browser_click", "Click an element in a browser tab for UI automation."),
    ("list_directory", "List files and subdirectories in a folder path."),
    ("delete_file", "Permanently delete a file at the given path."),
    ("grep_search", "Search file contents for a regex pattern across the codebase."),
    ("git_commit", "Create a git commit with staged changes and a message."),
    ("git_diff", "Show unstaged and staged diffs for tracked files."),
    ("git_push", "Push local commits to the remote git repository."),
    ("sql_query", "Run a read-only SQL query against a PostgreSQL database."),
    ("sql_migrate", "Apply pending schema migrations to the database."),
    ("send_email", "Send an email message to one or more recipients."),
    ("slack_post", "Post a message to a Slack channel or thread."),
    ("calendar_create", "Create a calendar event with title, time, and attendees."),
    ("translate_text", "Translate text between languages using a machine translation API."),
    ("summarize_doc", "Summarize a long document or article into bullet points."),
    ("generate_image", "Generate an image from a text prompt using a diffusion model."),
    ("ocr_pdf", "Extract text from a PDF document using optical character recognition."),
    ("fetch_url", "Download and return the body of an HTTP URL."),
    ("weather_lookup", "Get current weather and forecast for a city or coordinates."),
    ("stock_quote", "Fetch latest stock price and market data for a ticker symbol."),
    ("run_tests", "Execute the project test suite and return pass or fail results."),
    ("format_code", "Apply the project formatter to source files in a directory."),
    ("lint_code", "Run the linter on source files and report diagnostics."),
    ("deploy_service", "Deploy the application to a staging or production environment."),
    ("rotate_secret", "Rotate an API key or secret in the secrets manager."),
    ("health_check", "Ping service health endpoints and report uptime status."),
    ("mcp__context7", _MCP_CONTEXT7_DESC),
    ("mcp__context7_libraryId", _MCP_CONTEXT7_LIBRARYID_DESC),
    ("mcp__context7_resolve_library_id", _MCP_CONTEXT7_RESOLVE_LIBRARY_ID_DESC),
]


def _catalog_chunk(tool_id: str, description: str) -> dict[str, Any]:
    """Minimal decomposed-catalog shape that extract_json_catalog_document understands."""
    return {
        "file_path": f"tools/{tool_id}.json",
        "content": {
            "inputSchema": {
                "type": "object",
                "properties": {
                    "input": {
                        "type": "string",
                        "description": description,
                    },
                },
            },
        },
    }


def _build_catalog() -> dict[str, Any]:
    return {"json": [_catalog_chunk(tid, desc) for tid, desc in TOOL_DESCRIPTIONS], "md": []}


def _description_for_item(item: dict[str, Any]) -> str:
    text = extract_json_catalog_document(item)
    return text or "(no extractable text)"


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    catalog = _build_catalog()
    before = {item["file_path"]: _description_for_item(item) for item in catalog["json"]}

    with tempfile.TemporaryDirectory(prefix="cyt-bm25-scratch-") as tmp:
        config: dict[str, Any] = {
            "models": {
                "bm25": {
                    "index_dir": str(Path(tmp) / "bm25"),
                    "mmap": False,
                    "stem_language": "english",
                    "stopwords": "en",
                },
            },
            "pruning": {"tools": {"sequence": ["bm25"], "policy": {"per_tool": {}}}},
        }
        scored, _usage = bm25_catalog_dict(
            copy.deepcopy(catalog),
            query,
            prune=True,
            config=config,
        )

    kept = scored.get("json") or []
    if not kept:
        print(f"Query: {query!r}")
        print(f"No tools passed BM25 prune threshold ({BM25_SCORE}).")
        print("All candidates:")
        for path, desc in before.items():
            print(f"  - {path}: {desc}")
        sys.exit(1)

    print(f"Query: {query!r}")
    print(f"Relevant tools (score >= {BM25_SCORE}):")
    for item in kept:
        path = item.get("file_path", "?")
        score = float(item.get("score", 0))
        print(f"  [{score:.6f}] {path}")
        print(f"           {before.get(path, _description_for_item(item))}")


if __name__ == "__main__":
    main()
