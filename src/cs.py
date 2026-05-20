import codecs
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated, Any

import typer
from cocoindex_code.cli import (
    _catch_daemon_start_error,
    _run_index_with_progress,
    _search_with_wait_spinner,
    print_search_results,
    require_project_root,
)
from cocoindex_code.cli import (
    app as ccc_app,
)
from cocoindex_code.protocol import SearchResponse, SearchResult

from build_index import tool_id_from_decomposed_rel
from retrieve_catalog import to_decomposed_key

app = typer.Typer(help="Code search wrapper.")


def _entry_id_from_result(file_path: str, content: Any, *, is_json: bool) -> str:
    if is_json and isinstance(content, dict) and content.get("id"):
        return str(content["id"])
    decomposed_key = to_decomposed_key(file_path)
    if decomposed_key is not None:
        return tool_id_from_decomposed_rel(decomposed_key)
    return Path(file_path).stem


def _format_json_output(
    results_md: list[SearchResult],
    results_json: list[SearchResult],
) -> None:
    """Format and print search results in JSON format."""
    output: dict[str, list[dict[str, Any]]] = {"md": [], "json": []}

    for r in results_md:
        content = r.content
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        output["md"].append(
            {
                "id": _entry_id_from_result(r.file_path, content, is_json=False),
                "file_path": r.file_path,
                "score": round(r.score, 3),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "language": r.language,
                "content": content,
            },
        )

    for r in results_json:
        content = r.content
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            pass
        output["json"].append(
            {
                "id": _entry_id_from_result(r.file_path, content, is_json=True),
                "file_path": r.file_path,
                "score": round(r.score, 3),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "language": r.language,
                "content": content,
            },
        )

    sys.stdout.write(json.dumps(output, indent=2) + "\n")


def _print_file_only_results(
    results_md: list[SearchResult],
    results_json: list[SearchResult],
    separator: str,
) -> None:
    """Print only file paths separated by the given separator."""
    paths = [r.file_path for r in results_md] + [r.file_path for r in results_json]
    # Maintain order but remove duplicates
    unique_paths: list[str] = []
    seen = set()
    for p in paths:
        if p not in seen:
            unique_paths.append(f'"{p}"')
            seen.add(p)

    if unique_paths:
        sys.stdout.write(separator.join(unique_paths) + "\n")


def _print_standard_results(
    results_md: list[SearchResult],
    results_json: list[SearchResult],
) -> None:
    """Print search results using the standard format."""
    combined_results = results_md + results_json
    final_resp = SearchResponse(
        success=True,
        results=combined_results,
        total_returned=len(combined_results),
        offset=0,
        message=None,
    )
    print_search_results(final_resp)


def _search_with_retry(
    project_root: str,
    query: str,
    languages: list[str] | None = None,
    paths: list[str] | None = None,
    limit: int = 10,
    offset: int = 0,
    max_retries: int = 6,
    initial_delay: float = 0.05,
) -> SearchResponse:
    """Wrapper for _search_with_wait_spinner that retries on ConnectionRefusedError."""
    last_err = None
    for attempt in range(max_retries):
        try:
            return _search_with_wait_spinner(
                project_root=project_root,
                query=query,
                languages=languages,
                paths=paths,
                limit=limit,
                offset=offset,
            )
        except ConnectionRefusedError as e:
            last_err = e
            if attempt < max_retries - 1:
                delay = initial_delay * (2**attempt)
                time.sleep(delay)
            continue
    raise last_err or ConnectionRefusedError("Max retries reached")


def _get_project_root() -> str:
    """Determine the project root from environment or file system."""
    project_root_env = os.environ.get("COCOINDEX_PROJECT_ROOT")
    if project_root_env:
        return str(Path(project_root_env).absolute())
    return str(require_project_root())


def _perform_dual_search(
    project_root: str,
    query_str: str,
    lang_list: list[str],
    search_path: str,
    limit: int,
) -> tuple[list[SearchResult], list[SearchResult]]:
    """Execute dual-search aggregation logic in parallel."""

    def search_md() -> list[SearchResult]:
        """Search A: Exhaustive Markdown Search (non-recursive)."""
        all_results = []
        current_offset = 0
        page_limit = limit
        md_path = f"{search_path.rstrip('/')}/*.md"

        while True:
            resp = _search_with_retry(
                project_root=project_root,
                query=query_str,
                languages=lang_list or None,
                paths=[md_path],
                limit=page_limit,
                offset=current_offset,
            )
            if not resp.success:
                break
            all_results.extend(resp.results)
            if len(resp.results) < page_limit:
                break
            current_offset += page_limit
        return all_results

    def search_json() -> list[SearchResult]:
        """Search B: Recursive JSON Search."""
        json_path = f"{search_path.rstrip('/')}/**/*.json"
        resp = _search_with_retry(
            project_root=project_root,
            query=query_str,
            languages=lang_list or None,
            paths=[json_path],
            limit=limit,
            offset=0,
        )
        return resp.results if resp.success else []

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_md = executor.submit(search_md)
        future_json = executor.submit(search_json)
        return future_md.result(), future_json.result()


def _apply_root_prefix(
    results_md: list[SearchResult],
    results_json: list[SearchResult],
) -> None:
    """Prepend original root prefix to file paths if specified in environment."""
    root_prefix = os.environ.get("SCA_ROOT_STR")
    if root_prefix:
        for r in results_md:
            r.file_path = f"{root_prefix}{r.file_path}"
        for r in results_json:
            r.file_path = f"{root_prefix}{r.file_path}"


def _handle_search_output(
    results_md: list[SearchResult],
    results_json: list[SearchResult],
    json_output: bool,
    file_only: str | None,
) -> None:
    """Handle different output formats for search results."""
    if json_output:
        _format_json_output(results_md, results_json)
    elif file_only is not None:
        sep = file_only if file_only else " "
        try:
            sep = codecs.decode(sep, "unicode_escape")
        except Exception:
            pass
        _print_file_only_results(results_md, results_json, sep)
    else:
        _print_standard_results(results_md, results_json)


@app.command()
@_catch_daemon_start_error
def search(
    query: Annotated[list[str], typer.Argument(help="Search query")],
    lang: Annotated[list[str] | None, typer.Option("--lang", help="Filter by language")] = None,
    path: Annotated[
        str,
        typer.Option("--path", help="Filter by file path glob"),
    ] = "schemas/decomposed/",
    offset: Annotated[int, typer.Option("--offset", help="Number of results to skip")] = 0,
    limit: Annotated[int, typer.Option("--limit", help="Maximum results to return")] = 10,
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Refresh index before searching"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output results in JSON format"),
    ] = False,
    file_only: Annotated[
        str | None,
        typer.Option(
            "--file-only",
            help="Print only file paths with optional separator.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Semantic search across the codebase."""
    if json_output and file_only is not None:
        typer.secho(
            "Error: --json and --file-only cannot be used together.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    project_root = _get_project_root()
    query_str = " ".join(query)
    lang_list = lang or []

    if refresh:
        _run_index_with_progress(project_root)

    results_md, results_json = _perform_dual_search(project_root, query_str, lang_list, path, limit)

    _apply_root_prefix(results_md, results_json)
    _handle_search_output(results_md, results_json, json_output, file_only)


# Pass-through all other commands from ccc_app
for command in ccc_app.registered_commands:
    name = command.name or (
        getattr(command.callback, "__name__", None) if command.callback else None
    )
    if name != "search":
        app.registered_commands.append(command)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    root: Annotated[
        Path | None,
        typer.Option(
            "--root",
            help="Root directory for the project.",
        ),
    ] = None,
) -> None:
    """
    Code search wrapper.
    """
    if root:
        root_str = str(root)
        if not root_str.endswith("/"):
            root_str += "/"
        os.environ["SCA_ROOT_STR"] = root_str
        os.environ["COCOINDEX_PROJECT_ROOT"] = str(root.absolute())
    elif "COCOINDEX_PROJECT_ROOT" not in os.environ:
        os.environ["COCOINDEX_PROJECT_ROOT"] = os.getcwd()

    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


if __name__ == "__main__":
    app()
