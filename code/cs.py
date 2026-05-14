import json
import os
import sys
from pathlib import Path

import typer
from cocoindex_code.cli import (
    _catch_daemon_start_error,
    _run_index_with_progress,
    _search_with_wait_spinner,
    print_search_results,
    require_project_root,
    resolve_default_path,
)
from cocoindex_code.cli import (
    app as ccc_app,
)

app = typer.Typer(help="Code search wrapper.")


@app.command()
@_catch_daemon_start_error
def search(
    query: list[str] = typer.Argument(..., help="Search query"),
    lang: list[str] = typer.Option([], "--lang", help="Filter by language"),
    path: str | None = typer.Option(None, "--path", help="Filter by file path glob"),
    offset: int = typer.Option(0, "--offset", help="Number of results to skip"),
    limit: int = typer.Option(10, "--limit", help="Maximum results to return"),
    refresh: bool = typer.Option(False, "--refresh", help="Refresh index before searching"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
) -> None:
    """Semantic search across the codebase."""
    # Respect --root via environment variable set in main callback
    project_root_env = os.environ.get("COCOINDEX_PROJECT_ROOT")
    if project_root_env:
        project_root = str(Path(project_root_env).absolute())
    else:
        project_root = str(require_project_root())

    query_str = " ".join(query)

    if refresh:
        _run_index_with_progress(project_root)

    # Default path filter from CWD
    paths: list[str] | None = None
    if path is not None:
        paths = [path]
    else:
        default = resolve_default_path(Path(project_root))
        if default is not None:
            paths = [default]

    resp = _search_with_wait_spinner(
        project_root=project_root,
        query=query_str,
        languages=lang or None,
        paths=paths,
        limit=limit,
        offset=offset,
    )

    if json_output:
        results = []
        for r in resp.results:
            content = r.content
            try:
                # If content is valid JSON, represent it as nested JSON object
                content_json = json.loads(content)
                content = content_json
            except (json.JSONDecodeError, TypeError):
                pass

            results.append(
                {
                    "file_path": r.file_path,
                    "score": round(r.score, 3),
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "language": r.language,
                    "content": content,
                },
            )
        sys.stdout.write(json.dumps({"results": results}, indent=2) + "\n")
    else:
        print_search_results(resp)


# Pass-through all other commands from ccc_app
for command in ccc_app.registered_commands:
    name = command.name or (command.callback.__name__ if command.callback else None)
    if name != "search":
        app.registered_commands.append(command)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    root: Path = typer.Option(
        None,
        "--root",
        help="Root directory for the project.",
    ),
):
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
