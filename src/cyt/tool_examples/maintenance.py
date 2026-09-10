"""Background retention and schema-drift cleanup for tool examples."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from cyt.tool_examples.config import examples_active, tool_examples_config
from cyt.tool_examples.flatten import flatten_args
from cyt.tool_examples.store import ToolExamplesStore

logger = logging.getLogger(__name__)


def valid_paths_for_capture(schema: dict[str, Any], args: dict[str, Any]) -> set[str]:
    paths = {item.json_path for item in flatten_args(args)}
    paths.update(_valid_paths_from_schema(schema))
    return paths


def _valid_paths_from_schema(schema: dict[str, Any], prefix: str = "inputSchema.properties") -> set[str]:
    paths: set[str] = set()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return paths
    for key, spec in properties.items():
        path = f"{prefix}.{key}"
        paths.add(path)
        if isinstance(spec, dict):
            if spec.get("type") == "object":
                paths.update(_valid_paths_from_schema(spec, path))
            elif spec.get("type") == "array":
                items = spec.get("items")
                if isinstance(items, dict) and items.get("type") == "object":
                    paths.update(_valid_paths_from_schema(items, f"{path}.items[].properties"))
    return paths


def run_tool_examples_maintenance(config: dict[str, Any]) -> None:
    if not examples_active(config):
        return
    cfg = tool_examples_config(config)
    store = ToolExamplesStore.open(cfg.db_path)
    try:
        cutoff_ms = int(time.time() * 1000) - cfg.max_age_days * 86400 * 1000
        store.prune_stale_examples(cutoff_ms=cutoff_ms, min_per_path=cfg.min_per_path)
        store.enforce_example_path_limit(
            max_per_path=cfg.max_per_path,
            min_per_path=cfg.min_per_path,
        )
        store.enforce_capture_retention(
            max_captures=cfg.max_captures_per_tool,
            min_captures=cfg.min_captures_per_tool,
        )
        store.cleanup_orphan_example_paths()
    except Exception as exc:
        logger.warning("tool examples maintenance failed: %s", exc)
    finally:
        store.close()


def schedule_tool_examples_maintenance(config: dict[str, Any]) -> None:
    if not examples_active(config):
        return
    threading.Thread(
        target=run_tool_examples_maintenance,
        args=(config,),
        name="cyt-tool-examples-maintenance",
        daemon=True,
    ).start()
