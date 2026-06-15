"""Record skills injection events in stats.db."""

from __future__ import annotations

from typing import Any

from cyt.config import load_config, stats_db_path
from cyt.proxy.stats import StatsDB


def record_skills_injection(
    *,
    query: str,
    model_name: str,
    skills_in: int,
    request_tokens: int = 0,
    inject_path: str = "hook",
    skills_final_md: str | None = None,
    config: dict[str, Any] | None = None,
) -> str | None:
    """Persist a skills injection event; returns proxy_request id or None."""
    if skills_in <= 0:
        return None
    cfg = config or load_config()
    db = StatsDB.open(stats_db_path(cfg))
    try:
        return db.record_skills_injection(
            query=query,
            model_name=model_name,
            skills_in=skills_in,
            request_tokens=request_tokens,
            inject_path=inject_path,
            skills_final_md=skills_final_md,
            config=cfg,
        )
    finally:
        db.close()
