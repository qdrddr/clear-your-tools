"""Record successful tool captures into the examples store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cyt.tiers.config import resolve_project_root_path
from cyt.tool_examples.config import examples_active, tool_examples_config
from cyt.tool_examples.flatten import flatten_args
from cyt.tool_examples.maintenance import valid_paths_for_capture
from cyt.tool_examples.store import ToolExamplesStore


def record_tool_examples_capture(
    *,
    workspace: Path | None,
    mcp_server: str,
    tool_name: str,
    input_schema: dict[str, Any],
    args: dict[str, Any],
    config: dict[str, Any],
) -> int | None:
    if not examples_active(config):
        return None
    project_root = resolve_project_root_path(workspace=workspace)
    if project_root is None:
        return None
    cfg = tool_examples_config(config)
    valid_paths = valid_paths_for_capture(input_schema, args)
    flattened = [
        item
        for item in flatten_args(args, redact_key_patterns=cfg.redact_key_patterns)
        if item.json_path in valid_paths
    ]
    store = ToolExamplesStore.open(cfg.db_path)
    try:
        project_id = store.get_or_create_project(str(project_root))
        return store.upsert_capture(
            project_id,
            mcp_server,
            tool_name,
            input_schema,
            args,
            flattened=flattened,
        )
    finally:
        store.close()
