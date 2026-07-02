"""Record tools hook injection events in stats.db."""

from __future__ import annotations

from typing import Any

from cyt.common.token_usage import StageTokenUsage
from cyt.config import load_config, stats_db_path
from cyt.proxy.stats import StatsDB


def record_tools_hook_injection(
    *,
    query: str,
    model_name: str,
    tools_in: int,
    tools_out: int,
    prompt_tokens: int,
    pruning_stages: dict[str, StageTokenUsage] | None = None,
    tools_final_md: str | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Persist a tools-hook injection event; returns proxy_request id or None."""
    if tools_out <= 0:
        return None
    cfg = config or load_config()
    db = StatsDB.open(stats_db_path(cfg))
    try:
        return db.record_tools_hook_injection(
            query=query,
            model_name=model_name,
            tools_in=tools_in,
            tools_out=tools_out,
            prompt_tokens=prompt_tokens,
            pruning_stages=pruning_stages or {},
            tools_final_md=tools_final_md,
            config=cfg,
        )
    finally:
        db.close()
