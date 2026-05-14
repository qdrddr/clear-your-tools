import os
import sys
import logging
from pathlib import Path
from typing import Any

import psutil

logger = logging.getLogger(__name__)

def is_self_recursion(
    s_name: str,
    s_config: dict[str, Any],
    current_script: Path,
    mcp_name: str,
) -> bool:
    if s_name == mcp_name or "aggregator" in s_name.lower():
        logger.warning("Skipping server '%s' - appears to be an aggregator", s_name)
        return True

    cmd: str | None = s_config.get("command")
    args_raw: list[str] | str = s_config.get("args", [])
    parts = [cmd] + (args_raw if isinstance(args_raw, list) else [])
    for part in parts:
        if not part or not isinstance(part, str):
            continue
        try:
            p = Path(part)
            resolved = p.resolve()
            if resolved == current_script:
                return True
            if (
                resolved.is_file()
                and resolved.parent == current_script.parent
                and "aggregator" in resolved.name.lower()
            ):
                logger.warning(
                    "Skipping server '%s' - sibling aggregator script: %s",
                    s_name,
                    resolved.name,
                )
                return True
        except Exception:
            continue
    return False

def is_mcp_aggregator_description(description: str, mcp_name: str) -> bool:
    """Check if the MCP server description/name matches 'MCP Aggregator'."""
    if description == mcp_name or description.lower() == "mcp aggregator":
        logger.warning("Detected self-recursion via MCP description: '%s'", description)
        return True
    return False

def check_self_recursion_protection() -> None:
    """Check if another aggregator is already running via SCA_AGGREGATOR_PID."""
    agg_pid_str = os.environ.get("SCA_AGGREGATOR_PID")
    if agg_pid_str:
        try:
            agg_pid = int(agg_pid_str)
            if psutil.pid_exists(agg_pid):
                proc = psutil.Process(agg_pid)
                # Use cmdline() for more robust name checking as name() might just be 'python'
                proc_desc = " ".join(proc.cmdline()).lower()
                if "aggregator" in proc_desc or "aggregator" in proc.name().lower():
                    logger.warning(
                        "Another aggregator (PID %d) is already running. Exiting to prevent self-recursion.",
                        agg_pid,
                    )
                    sys.exit(0)
        except (ValueError, psutil.NoSuchProcess, psutil.AccessDenied):
            # PID is invalid or process already dead
            pass

    os.environ["SCA_AGGREGATOR_PID"] = str(os.getpid())
    logger.info("Registered current aggregator PID: %d", os.getpid())
