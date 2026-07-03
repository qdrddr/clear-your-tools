"""Cache policy helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cyt.config import cache_enabled, load_config
from cyt.pruners.policies import PolicyContext, policy_context_from_config


def cache_policy_for_config(config: dict[str, Any] | None = None) -> str:
    """Return cyt-indexer cache policy: auto or force_memory when cache disabled."""
    cfg = config or load_config()
    return "auto" if cache_enabled(cfg) else "force_memory"


def tools_catalog_policy_fingerprint(
    config: dict[str, Any],
    *,
    ctx: PolicyContext | None = None,
) -> str:
    """Stable hash for tool decompose cache keys (policy-aware)."""
    resolved = ctx or policy_context_from_config(config=config)
    payload = {
        "system_tool": resolved.system_policy,
        "mcp_tool": resolved.mcp_policy,
    }
    per_tool = config.get("pruning", {})
    if isinstance(per_tool, dict):
        tools_cfg = per_tool.get("tools")
        if isinstance(tools_cfg, dict):
            policy_cfg = tools_cfg.get("policy")
            if isinstance(policy_cfg, dict) and isinstance(policy_cfg.get("per_tool"), dict):
                payload["per_tool"] = policy_cfg["per_tool"]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
