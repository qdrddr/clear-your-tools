"""Tier configuration resolution."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from cyt.config.sections import tools_at
from cyt.hook.install_scope import CytInstallScope

TierToolCaptureSource = Literal["cyt_mcp", "hooks"]


class TierMode(StrEnum):
    """Tier operating mode: off (disabled), shadow (observe only), live (apply filtering)."""

    OFF = "off"
    SHADOW = "shadow"
    LIVE = "live"

    @property
    def is_active(self) -> bool:
        return self != TierMode.OFF

    @property
    def applies(self) -> bool:
        return self == TierMode.LIVE


@dataclass(frozen=True)
class TierThresholds:
    promote_demand: float
    demote_demand: float
    promote_utility: float
    demote_utility: float


@dataclass(frozen=True)
class TierSectionConfig:
    mode: TierMode
    request_half_life: float
    prompt_cache_ttl_minutes: float
    ttl_multiplier: float
    idle_gap_triggers_epoch: bool
    min_injections_before_reconsider: int
    wake_threshold: float
    wake_lease_cycles: int
    sleep_cooldown_cycles: int
    emergency_t4_inject_min: int
    emergency_t4_utility_max: float
    temp_promotion_turns: int
    thresholds_t12: TierThresholds
    thresholds_t23: TierThresholds
    thresholds_t34: TierThresholds
    wake_weights: dict[str, float]


def _float(value: object, default: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _int(value: object, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return default


def _bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _parse_tier_mode(value: str) -> TierMode | None:
    normalized = value.strip().lower()
    try:
        return TierMode(normalized)
    except ValueError:
        return None


def _resolve_tier_mode(*sources: dict[str, Any]) -> TierMode:
    for source in sources:
        raw_mode = source.get("mode")
        if isinstance(raw_mode, str):
            parsed = _parse_tier_mode(raw_mode)
            if parsed is not None:
                return parsed
    return TierMode.SHADOW


def _threshold_pair(section: dict[str, Any], prefix: str) -> TierThresholds:
    return TierThresholds(
        promote_demand=_float(section.get(f"{prefix}_promote_demand"), 0.55),
        demote_demand=_float(section.get(f"{prefix}_demote_demand"), 0.30),
        promote_utility=_float(section.get(f"{prefix}_promote_utility"), 0.35),
        demote_utility=_float(section.get(f"{prefix}_demote_utility"), 0.20),
    )


def _tiers_block(cfg: dict[str, Any], *, kind: str) -> dict[str, Any]:
    if kind == "tool":
        block = tools_at(cfg, "tiers")
    else:
        skills = cfg.get("skills")
        block = skills.get("tiers") if isinstance(skills, dict) else None
    if isinstance(block, dict):
        return block
    return {}


def _merge_tiers_blocks(cfg: dict[str, Any], *, kind: str) -> dict[str, Any]:
    tools_block = _tiers_block(cfg, kind="tool")
    if kind == "tool":
        return dict(tools_block)
    skills_block = _tiers_block(cfg, kind="skill")
    merged = dict(tools_block)
    merged.update(skills_block)
    return merged


def tier_section_config(cfg: dict[str, Any], *, kind: str) -> TierSectionConfig:
    block = _merge_tiers_blocks(cfg, kind=kind)
    stats = block.get("statistics")
    stats_dict = stats if isinstance(stats, dict) else {}
    epoch = block.get("cache_epoch")
    epoch_dict = epoch if isinstance(epoch, dict) else {}
    evaluation = block.get("evaluation")
    eval_dict = evaluation if isinstance(evaluation, dict) else {}
    wake = block.get("wake")
    wake_dict = wake if isinstance(wake, dict) else {}
    thresholds = block.get("thresholds")
    thresholds_dict = thresholds if isinstance(thresholds, dict) else {}
    t12 = thresholds_dict.get("t12")
    t23 = thresholds_dict.get("t23")
    t34 = thresholds_dict.get("t34")
    t12_dict = t12 if isinstance(t12, dict) else {}
    t23_dict = t23 if isinstance(t23, dict) else {}
    t34_dict = t34 if isinstance(t34, dict) else {}
    wake_weights = wake_dict.get("weights")
    weights = wake_weights if isinstance(wake_weights, dict) else {}
    kind_block = _tiers_block(cfg, kind=kind)
    mode = _resolve_tier_mode(kind_block, block)
    return TierSectionConfig(
        mode=mode,
        request_half_life=_float(stats_dict.get("request_half_life"), 100.0),
        prompt_cache_ttl_minutes=_float(epoch_dict.get("prompt_cache_ttl_minutes"), 5.0),
        ttl_multiplier=_float(epoch_dict.get("ttl_multiplier"), 1.0),
        idle_gap_triggers_epoch=_bool(epoch_dict.get("idle_gap_triggers_epoch"), True),
        min_injections_before_reconsider=_int(
            eval_dict.get("min_injections_before_reconsider"),
            8,
        ),
        wake_threshold=_float(wake_dict.get("threshold"), 0.60),
        wake_lease_cycles=_int(
            wake_dict.get("lease_cycles", wake_dict.get("lease_sessions")),
            2,
        ),
        sleep_cooldown_cycles=_int(
            wake_dict.get("cooldown_cycles", wake_dict.get("cooldown_sessions")),
            2,
        ),
        emergency_t4_inject_min=_int(block.get("emergency_t4_inject_min"), 20),
        emergency_t4_utility_max=_float(block.get("emergency_t4_utility_max"), 0.20),
        temp_promotion_turns=_int(block.get("temp_promotion_turns"), 3),
        thresholds_t12=_threshold_pair(t12_dict, "t12"),
        thresholds_t23=_threshold_pair(t23_dict, "t23"),
        thresholds_t34=_threshold_pair(t34_dict, "t34"),
        wake_weights={
            "shadow": _float(weights.get("shadow"), 0.40),
            "direct": _float(weights.get("direct"), 0.30),
            "query": _float(weights.get("query"), 0.20),
            "sibling": _float(weights.get("sibling"), 0.10),
        },
    )


def tier_state_db_path(cfg: dict[str, Any]) -> str:
    block = _tiers_block(cfg, kind="tool")
    database = block.get("database")
    if isinstance(database, dict):
        path = database.get("path")
        if isinstance(path, str) and path.strip():
            return str(Path(path).expanduser())
    return str(Path("~/.config/cyt/tier_state.db").expanduser())


def tier_disk_flush_seconds(cfg: dict[str, Any]) -> float:
    """Interval for background tier-stat disk flush; <= 0 means synchronous flush."""
    block = _tiers_block(cfg, kind="tool")
    database = block.get("database")
    if isinstance(database, dict):
        return _float(database.get("disk_flush_seconds"), 900.0)
    return 900.0


def resolve_git_toplevel(path: Path) -> Path | None:
    """Return git repository root for *path*, or *path* when not inside a repo."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        resolved = resolved.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=0.2,
            check=False,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top:
                return Path(top).expanduser().resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    current = resolved
    for _ in range(64):
        if (current / ".git").exists():
            return current.resolve()
        parent = current.parent
        if parent == current:
            break
        current = parent
    return resolved if resolved.is_dir() else None


def _resolve_git_repo_root(path: Path) -> Path | None:
    """Return git repository root for *path*, or None when not inside a repo."""
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        resolved = resolved.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(resolved), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=0.2,
            check=False,
        )
        if result.returncode == 0:
            top = result.stdout.strip()
            if top:
                return Path(top).expanduser().resolve()
    except (OSError, subprocess.TimeoutExpired):
        pass
    current = resolved
    for _ in range(64):
        if (current / ".git").exists():
            return current.resolve()
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def cyt_package_git_root() -> Path | None:
    """Git root of the installed ``cyt`` package, when available."""
    from cyt.hook.workspace_resolution import cyt_package_git_root as _cyt_package_git_root

    return _cyt_package_git_root()


def resolve_project_root_path(*, workspace: Path | None = None) -> Path | None:
    """Resolve canonical project root from workspace path or cwd."""
    from cyt.hook.workspace_resolution import resolve_consumer_project_root

    return resolve_consumer_project_root(workspace=workspace)


def resolve_tier_project(*, workspace: Path | None = None) -> Path | None:
    """Return git repository root for tier scoping, if any."""
    from cyt.common.paths import is_ephemeral_workspace_path

    root = resolve_project_root_path(workspace=workspace)
    if root is None:
        return None
    if is_ephemeral_workspace_path(root):
        return None
    return root


def resolve_tier_scope(*, workspace: Path | None = None) -> Path | None:
    """Deprecated name for project-root resolution."""
    return resolve_tier_project(workspace=workspace)


def tier_tool_capture_source(cfg: dict[str, Any]) -> TierToolCaptureSource:
    """Return where tool-use tier feedback is captured (default: cyt_mcp)."""
    block = _tiers_block(cfg, kind="tool")
    capture = block.get("capture")
    capture_dict = capture if isinstance(capture, dict) else {}
    raw = capture_dict.get("source")
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_")
        if normalized == "hooks":
            return "hooks"
        if normalized in {"cyt_mcp", "cytmcp"}:
            return "cyt_mcp"
    return "cyt_mcp"


def tier_tool_capture_via_hooks(cfg: dict[str, Any]) -> bool:
    return tier_tool_capture_source(cfg) == "hooks"


def tiers_active(cfg: dict[str, Any], *, kind: str) -> bool:
    section = tier_section_config(cfg, kind=kind)
    return section.mode.is_active


def tiers_apply(cfg: dict[str, Any], *, kind: str) -> bool:
    section = tier_section_config(cfg, kind=kind)
    return section.mode.applies


def skills_tier_prompt_eval_active(cfg: dict[str, Any]) -> bool:
    """True when hook/proxy should run tier-scoped skill search on user prompts."""
    from cyt.config import skills_enabled

    return skills_enabled(cfg) and tiers_active(cfg, kind="skill")


def resolve_tier_status_agent(
    config: dict[str, Any],
    *,
    workspace_root: Path | None,
    explicit: str | None = None,
) -> str:
    """Resolve agent for ``tiers stats`` skill scoping (CLI override or mcp-config default)."""
    if isinstance(explicit, str) and explicit.strip():
        from cyt.launch.upstream import parse_agent_name

        return parse_agent_name(explicit.strip())

    from cyt_mcp.config import GLOBAL_MCP_CONFIG_PATH, load_mcp_config_yaml

    install = CytInstallScope(workspace_root=workspace_root)
    candidates: list[Path] = []
    ws_mcp = install.workspace_all_agents_cyt_mcp_config_path()
    if ws_mcp is not None:
        candidates.append(ws_mcp)
    candidates.append(GLOBAL_MCP_CONFIG_PATH)

    seen: set[str] = set()
    for path in candidates:
        resolved = path.expanduser()
        key = str(resolved)
        if key in seen or not resolved.is_file():
            continue
        seen.add(key)
        raw = load_mcp_config_yaml(resolved)
        default = raw.get("default_agent")
        if isinstance(default, str) and default.strip():
            from cyt.launch.upstream import parse_agent_name

            return parse_agent_name(default.strip())

    from cyt.skills.agents import resolve_skills_agent

    skills_agent = resolve_skills_agent()
    if skills_agent is not None:
        return skills_agent

    return "cursor"
