import json
import os
import sys
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

app = typer.Typer(help="Code search wrapper.")


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
                "file_path": r.file_path,
                "score": round(r.score, 3),
                "start_line": r.start_line,
                "end_line": r.end_line,
                "language": r.language,
                "content": content,
            },
        )

    sys.stdout.write(json.dumps(output, indent=2) + "\n")


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


@app.command()
@_catch_daemon_start_error
def search(
    query: Annotated[list[str], typer.Argument(help="Search query")],
    lang: Annotated[list[str] | None, typer.Option("--lang", help="Filter by language")] = None,
    path: Annotated[str | None, typer.Option("--path", help="Filter by file path glob")] = None,
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
) -> None:
    """Semantic search across the codebase."""
    # Respect --root via environment variable set in main callback
    project_root_env = os.environ.get("COCOINDEX_PROJECT_ROOT")
    if project_root_env:
        project_root = str(Path(project_root_env).absolute())
    else:
        project_root = str(require_project_root())

    query_str = " ".join(query)
    lang_list = lang or []

    if refresh:
        _run_index_with_progress(project_root)

    # Custom dual-search aggregation logic
    search_path = path if path is not None else "./"

    def search_md() -> list[SearchResult]:
        """Search A: Exhaustive Markdown Search (non-recursive)."""
        all_results = []
        current_offset = 0
        page_limit = limit
        md_path = f"{search_path.rstrip('/')}/*.md"

        while True:
            resp = _search_with_wait_spinner(
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
        resp = _search_with_wait_spinner(
            project_root=project_root,
            query=query_str,
            languages=lang_list or None,
            paths=[json_path],
            limit=limit,
            offset=0,
        )
        return resp.results if resp.success else []

    # Run both searches in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        future_md = executor.submit(search_md)
        future_json = executor.submit(search_json)

        results_md = future_md.result()
        results_json = future_json.result()

    if json_output:
        _format_json_output(results_md, results_json)
    else:
        _print_standard_results(results_md, results_json)


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
        os.environ["COCOINDEX_PROJECT_ROOT"] = str(root.absolute())
    elif "COCOINDEX_PROJECT_ROOT" not in os.environ:
        os.environ["COCOINDEX_PROJECT_ROOT"] = os.getcwd()

    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


if __name__ == "__main__":
    app()
