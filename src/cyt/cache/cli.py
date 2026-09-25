"""``cyt cache`` CLI — manual on-disk and in-memory cache clearing."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from cyt.config import (
    cache_bm25_dir,
    cache_skills_dir,
    cache_tools_dir,
    load_config,
)


def add_cache_parser(subparsers: argparse._SubParsersAction) -> None:
    cache_parser = subparsers.add_parser(
        "cache",
        help="On-disk and in-memory cache utilities",
    )
    cache_sub = cache_parser.add_subparsers(dest="cache_command", required=True)
    clear = cache_sub.add_parser(
        "clear",
        help="Clear cached data (disk and in-process). Does not run automatically.",
    )
    clear.add_argument(
        "--tools",
        action="store_true",
        help="Clear decomposed tool catalog cache",
    )
    clear.add_argument(
        "--skills",
        action="store_true",
        help="Clear skills page-index cache",
    )
    clear.add_argument(
        "--bm25",
        action="store_true",
        help="Clear BM25 Tantivy index cache",
    )
    clear.add_argument(
        "--catalog",
        action="store_true",
        help="Clear hook catalog envelopes (cyt-mcp, executor, mcpc, cloudflare) and registry snapshot",
    )
    clear.add_argument(
        "--all",
        action="store_true",
        help="Clear all cache layers (tools, skills, bm25, catalog)",
    )
    clear.set_defaults(cache_handler=run_cache_clear)


def _rmtree_quiet(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=False)
    return True


def _clear_memory_caches(*, tools: bool, skills: bool, catalog: bool) -> list[str]:
    cleared: list[str] = []
    if tools or catalog:
        from cyt.tools.catalog_cache import clear_decomposed_catalog_cache
        from cyt.tools.master_catalog import clear_master_catalog_cache

        clear_decomposed_catalog_cache()
        clear_master_catalog_cache()
        cleared.append("memory:decomposed-tools")
        cleared.append("memory:master-catalog")
    if catalog:
        from cyt.cloudflare.catalog import clear_cloudflare_catalog_cache
        from cyt.cyt_mcp import catalog as cyt_mcp_catalog
        from cyt.cyt_mcp.cache_scheduler import clear_cyt_mcp_cache_schedulers
        from cyt.executor.http import clear_executor_catalog_cache
        from cyt.hook.catalog_registry import clear_catalog_registry
        from cyt.mcpc.catalog import clear_mcpc_catalog_cache
        from cyt.tools.definitions_catalog import clear_definitions_catalog_cache

        with cyt_mcp_catalog._catalog_lock:
            cyt_mcp_catalog._catalog_states.clear()
        clear_cyt_mcp_cache_schedulers()
        clear_executor_catalog_cache()
        clear_mcpc_catalog_cache()
        clear_cloudflare_catalog_cache()
        clear_definitions_catalog_cache()
        clear_catalog_registry(purge_disk_snapshot=False)
        cleared.extend(
            [
                "memory:cyt-mcp-catalog",
                "memory:executor-catalog",
                "memory:mcpc-catalog",
                "memory:cloudflare-catalog",
                "memory:definitions-catalog",
                "memory:catalog-registry",
            ],
        )
    if skills:
        from cyt.skills.catalog import clear_registry_cache

        clear_registry_cache()
        cleared.append("memory:skills-registry")
    return cleared


def _clear_disk_catalog_envelopes() -> list[str]:
    from cyt.cloudflare.catalog_disk import cloudflare_catalog_cache_dir
    from cyt.cyt_mcp.catalog_disk import (
        clear_cyt_mcp_disk_catalog_cache,
        cyt_mcp_catalog_cache_dir,
    )
    from cyt.executor.catalog_disk import executor_catalog_cache_dir
    from cyt.hook.catalog_registry import REGISTRY_SNAPSHOT_FILE, clear_catalog_registry
    from cyt.mcpc.catalog_disk import mcpc_catalog_cache_dir

    cleared: list[str] = []
    clear_cyt_mcp_disk_catalog_cache()
    for path in (
        cyt_mcp_catalog_cache_dir(),
        executor_catalog_cache_dir(),
        mcpc_catalog_cache_dir(),
        cloudflare_catalog_cache_dir(),
    ):
        if _rmtree_quiet(path):
            cleared.append(f"disk:{path}")
    clear_catalog_registry(purge_disk_snapshot=True)
    if REGISTRY_SNAPSHOT_FILE.is_file():
        cleared.append(f"disk:{REGISTRY_SNAPSHOT_FILE}")
    return cleared


def _clear_disk_caches(
    config: dict[str, Any],
    *,
    tools: bool,
    skills: bool,
    bm25: bool,
    catalog: bool,
) -> list[str]:
    cleared: list[str] = []
    if tools:
        path = cache_tools_dir(config)
        if _rmtree_quiet(path):
            cleared.append(f"disk:{path}")
    if skills:
        path = cache_skills_dir(config)
        if _rmtree_quiet(path):
            cleared.append(f"disk:{path}")
    if bm25:
        path = cache_bm25_dir(config)
        if _rmtree_quiet(path):
            cleared.append(f"disk:{path}")
    if catalog:
        cleared.extend(_clear_disk_catalog_envelopes())
    return cleared


def run_cache_clear(args: argparse.Namespace) -> int:
    selected = bool(args.tools or args.skills or args.bm25 or args.catalog or args.all)
    if not selected:
        print(
            "cache clear: specify at least one of --tools, --skills, --bm25, --catalog, or --all",
            file=sys.stderr,
        )
        return 2

    tools = bool(args.tools or args.all)
    skills = bool(args.skills or args.all)
    bm25 = bool(args.bm25 or args.all)
    catalog = bool(args.catalog or args.all)
    config = load_config()

    cleared: list[str] = []
    cleared.extend(_clear_memory_caches(tools=tools, skills=skills, catalog=catalog))
    cleared.extend(
        _clear_disk_caches(
            config,
            tools=tools,
            skills=skills,
            bm25=bm25,
            catalog=catalog,
        ),
    )

    if not cleared:
        print("cache clear: nothing to remove (paths already absent)")
        return 0

    for line in cleared:
        print(f"cache clear: removed {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cyt cache")
    sub = parser.add_subparsers(dest="cache_command", required=True)
    clear = sub.add_parser("clear", help=argparse.SUPPRESS)
    clear.add_argument("--tools", action="store_true")
    clear.add_argument("--skills", action="store_true")
    clear.add_argument("--bm25", action="store_true")
    clear.add_argument("--catalog", action="store_true")
    clear.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)
    return run_cache_clear(args)
