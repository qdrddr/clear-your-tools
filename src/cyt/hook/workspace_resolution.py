"""Language-agnostic consumer workspace resolution for CLI, tiers, and cyt-mcp."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cyt.hook.install_scope import detect_workspace_root

# Option C (default): terminal.integrated.env in .vscode/settings.json — expanded by the IDE.
CYT_WORKSPACE_ENV = "CYT_WORKSPACE"
# Option A (fallback): set by ~/.cursor/hooks/cyt/uv.ps1 (or uv.sh) from shell cwd when C is unset.
CYT_SHELL_WORKSPACE_ENV = "CYT_SHELL_WORKSPACE"
CYT_UV_INVOCATION_FILENAME = "cyt-invocation.json"
CYT_UV_TOOL_PACKAGE = "clear-your-tools"
CYT_CLI_APP_SCRIPT_REL = "src/cyt/cli/app.py"
# Unix / Git-Bash on Windows: shell cwd survives ``uv --directory`` (process cwd does not).
_SHELL_CWD_FALLBACK_ENV_KEYS: tuple[str, ...] = ("PWD", "OLDPWD")

# Additional env keys treated like Option C (first match wins among these).
_TERMINAL_WORKSPACE_ENV_KEYS: tuple[str, ...] = (
    CYT_WORKSPACE_ENV,
    "WORKSPACE_FOLDER",
    "VSCODE_WORKSPACE_FOLDER",
    "CURSOR_WORKSPACE_FOLDER",
    "CYT_HOOK_CWD",
    "CYT_TIER_WORKSPACE",
)


class WorkspaceResolutionSource(StrEnum):
    EXPLICIT = "explicit"  # Option B: --workspace
    SHELL = "shell"  # Option A: CYT_SHELL_WORKSPACE
    TERMINAL_ENV = "terminal_env"  # Option C: CYT_WORKSPACE / IDE terminal env
    HOOK_CONFIG = "hook_config"
    CURSOR_LABEL = "cursor_label"
    ACTIVE_REGISTRY = "active_registry"
    CWD_MARKERS = "cwd_markers"
    CWD_GIT = "cwd_git"
    CYT_REPO_DEFAULT = "cyt_repo_default"


@dataclass(frozen=True, slots=True)
class WorkspaceResolution:
    root: Path | None
    source: WorkspaceResolutionSource | None = None
    detail: str | None = None


class WorkspaceResolutionConflictError(ValueError):
    """Raised when Option C (terminal env) and Option A (shell) disagree."""

    def __init__(self, *, sources: dict[str, Path]) -> None:
        parts = ", ".join(f"{name}={path}" for name, path in sorted(sources.items()))
        super().__init__(
            "conflicting workspace roots detected "
            f"({parts}). Use --workspace to override, or align "
            f".vscode/settings.json terminal.integrated.env {CYT_WORKSPACE_ENV} "
            "with the shell cwd (~/.cursor/hooks/cyt/uv.ps1).",
        )
        self.sources = sources


class WorkspacePathNotAbsoluteError(ValueError):
    """Raised when a workspace path uses ``~``, ``./``, ``../``, or other non-absolute forms."""

    def __init__(self, raw: str, *, label: str = "workspace") -> None:
        self.raw = raw
        self.label = label
        super().__init__(
            f"{label} must be a full absolute path (not ./, ../, or ~): {raw!r}",
        )


def _is_template_workspace_value(raw: str) -> bool:
    text = raw.strip()
    return text.startswith("${") and text.endswith("}")


def _workspace_path_raw_is_relative_or_home(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith("~"):
        return True
    if stripped in {".", ".."}:
        return True
    if stripped.startswith("./") or stripped.startswith(".\\"):
        return True
    if stripped.startswith("../") or stripped.startswith("..\\"):
        return True
    return not Path(stripped).is_absolute()


def require_absolute_workspace_dir(raw: str | Path, *, label: str = "workspace") -> Path:
    """Validate *raw* is a full absolute directory path and return its resolved form."""
    from cyt_client.rules_file import normalize_workspace_path_string

    text = normalize_workspace_path_string(str(raw).strip())
    if not text or _is_template_workspace_value(text):
        raise WorkspacePathNotAbsoluteError(str(raw), label=label)
    if _workspace_path_raw_is_relative_or_home(text):
        raise WorkspacePathNotAbsoluteError(str(raw), label=label)
    try:
        path = Path(text)
        resolved = path.resolve()
    except OSError as exc:
        raise WorkspacePathNotAbsoluteError(str(raw), label=label) from exc
    if not resolved.is_dir():
        raise ValueError(f"{label} is not an existing directory: {resolved}")
    return resolved


def absolute_workspace_arg(value: str) -> Path:
    """Argparse type: require a full absolute workspace directory path."""
    return require_absolute_workspace_dir(value, label="--workspace")


def _path_from_env_value(raw: str, *, label: str) -> Path | None:
    text = raw.strip()
    if not text or _is_template_workspace_value(text):
        return None
    if _workspace_path_raw_is_relative_or_home(text):
        raise WorkspacePathNotAbsoluteError(text, label=label)
    try:
        path = require_absolute_workspace_dir(text, label=label)
    except ValueError:
        return None
    return path


def _canonical_git_root(path: Path) -> Path | None:
    from cyt.tiers.config import resolve_git_toplevel

    return resolve_git_toplevel(path)


def _canonical_git_repo_root(path: Path) -> Path | None:
    from cyt.tiers.config import _resolve_git_repo_root

    return _resolve_git_repo_root(path)


def cyt_package_git_root() -> Path | None:
    """Git root of the installed ``cyt`` package checkout, when available."""
    from cyt.tiers.config import _resolve_git_repo_root

    try:
        import cyt

        return _resolve_git_repo_root(Path(cyt.__file__).resolve().parent)
    except (ImportError, OSError, TypeError, ValueError):
        return None


def _resolved_env_root(raw: str, *, label: str) -> Path | None:
    path = _path_from_env_value(raw, label=label)
    if path is None:
        return None
    return _canonical_git_root(path)


def _collect_option_ac_roots() -> dict[str, Path]:
    """Return resolved Option A / Option C roots when present."""
    roots: dict[str, Path] = {}
    shell_raw = os.environ.get(CYT_SHELL_WORKSPACE_ENV, "").strip()
    if shell_raw:
        shell_root = _resolved_env_root(
            shell_raw,
            label=f"${CYT_SHELL_WORKSPACE_ENV}",
        )
        if shell_root is not None:
            roots["shell"] = shell_root
    for key in _TERMINAL_WORKSPACE_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        terminal_root = _resolved_env_root(raw, label=f"${key}")
        if terminal_root is None:
            continue
        roots.setdefault("terminal", terminal_root)
        break
    return roots


def _assert_no_option_ac_conflict() -> None:
    roots = _collect_option_ac_roots()
    if len(roots) < 2:
        return
    unique = {str(root.resolve()) for root in roots.values()}
    if len(unique) > 1:
        raise WorkspaceResolutionConflictError(sources=roots)


def bootstrap_shell_workspace_env_for_hook() -> None:
    """Copy ``PWD`` / ``OLDPWD`` into ``CYT_SHELL_WORKSPACE`` for hook setup when unset."""
    if os.environ.get(CYT_SHELL_WORKSPACE_ENV, "").strip():
        return
    for key in _SHELL_CWD_FALLBACK_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        root = _resolved_env_root(raw, label=f"${key}")
        if root is None:
            continue
        os.environ[CYT_SHELL_WORKSPACE_ENV] = str(root.resolve())
        return


def _resolve_from_shell_env(*, include_pwd: bool = False) -> WorkspaceResolution | None:
    keys = [CYT_SHELL_WORKSPACE_ENV]
    if include_pwd:
        keys.extend(_SHELL_CWD_FALLBACK_ENV_KEYS)
    for key in keys:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        root = _resolved_env_root(raw, label=f"${key}")
        if root is None:
            continue
        return WorkspaceResolution(
            root=root,
            source=WorkspaceResolutionSource.SHELL,
            detail=key,
        )
    return None


def _resolve_from_terminal_env() -> WorkspaceResolution | None:
    for key in _TERMINAL_WORKSPACE_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        if not raw:
            continue
        root = _resolved_env_root(raw, label=f"${key}")
        if root is None:
            continue
        return WorkspaceResolution(
            root=root,
            source=WorkspaceResolutionSource.TERMINAL_ENV,
            detail=key,
        )
    return None


def _resolve_from_hook_config(config: dict[str, Any] | None) -> WorkspaceResolution | None:
    if config is None:
        return None
    from cyt.hook.workspace_config import hook_workspace_from_config

    workspace = hook_workspace_from_config(config)
    if workspace is None:
        return None
    root = _canonical_git_root(workspace)
    if root is None:
        return None
    return WorkspaceResolution(
        root=root,
        source=WorkspaceResolutionSource.HOOK_CONFIG,
        detail=CYT_WORKSPACE_ENV,
    )


def _resolve_from_cursor_label() -> WorkspaceResolution | None:
    label = os.environ.get("CURSOR_WORKSPACE_LABEL", "").strip()
    if not label:
        return None

    from cyt.config import load_config
    from cyt.tiers.config import tier_state_db_path
    from cyt.tiers.store import TierStore

    db_path = tier_state_db_path(load_config())
    store = TierStore.open(db_path)
    try:
        projects = store.list_projects()
    finally:
        store.close()

    matches = [row for row in projects if Path(str(row["root_path"])).name == label]
    if not matches:
        return None
    if len(matches) == 1:
        root = _canonical_git_root(Path(str(matches[0]["root_path"])))
    else:
        best = max(matches, key=lambda row: int(row.get("last_seen_ms") or 0))
        root = _canonical_git_root(Path(str(best["root_path"])))
    if root is None:
        return None
    return WorkspaceResolution(
        root=root,
        source=WorkspaceResolutionSource.CURSOR_LABEL,
        detail=label,
    )


def _resolve_from_active_registry(
    *,
    agent: str | None,
    exclude_root: Path | None,
) -> WorkspaceResolution | None:
    from cyt.hook.active_workspace import resolve_recent_active_workspace

    excluded: set[str] = set()
    if exclude_root is not None:
        try:
            excluded.add(str(exclude_root.expanduser().resolve()))
        except OSError:
            excluded.add(str(exclude_root))
    root = resolve_recent_active_workspace(agent or "cursor", exclude_roots=excluded)
    if root is None:
        return None
    canonical = _canonical_git_root(root)
    if canonical is None:
        return None
    return WorkspaceResolution(
        root=canonical,
        source=WorkspaceResolutionSource.ACTIVE_REGISTRY,
        detail=agent or "cursor",
    )


def resolve_consumer_workspace(
    *,
    workspace: Path | None = None,
    config: dict[str, Any] | None = None,
    agent: str | None = None,
) -> WorkspaceResolution:
    """Resolve the git repo root for the workspace consuming cyt (language-agnostic).

    Priority: Option B (explicit ``--workspace``) > Option C (``CYT_WORKSPACE`` /
    ``.vscode/settings.json`` terminal env) > Option A (``CYT_SHELL_WORKSPACE``)
    > hook config > cursor label > active registry > cwd fallbacks.
    """
    cwd = Path.cwd()
    cyt_root = cyt_package_git_root()

    if workspace is not None:
        abs_workspace = require_absolute_workspace_dir(workspace, label="--workspace")
        root = _canonical_git_root(abs_workspace)
        return WorkspaceResolution(
            root=root,
            source=WorkspaceResolutionSource.EXPLICIT if root else None,
            detail=str(abs_workspace),
        )

    _assert_no_option_ac_conflict()

    for resolver in (
        _resolve_from_terminal_env,
        _resolve_from_shell_env,
        lambda: _resolve_from_hook_config(config),
        _resolve_from_cursor_label,
    ):
        resolution = resolver()
        if resolution is not None and resolution.root is not None:
            return resolution

    cwd_detected = detect_workspace_root(cwd=cwd)
    cwd_git = _canonical_git_repo_root(cwd) if cwd_detected is None else _canonical_git_root(cwd_detected)

    if (
        cwd_git is not None
        and cyt_root is not None
        and cwd_git.resolve() == cyt_root.resolve()
    ):
        registry_resolution = _resolve_from_active_registry(
            agent=agent,
            exclude_root=cyt_root,
        )
        if registry_resolution is not None and registry_resolution.root is not None:
            return registry_resolution
        return WorkspaceResolution(
            root=cyt_root,
            source=WorkspaceResolutionSource.CYT_REPO_DEFAULT,
            detail="uv --directory or cyt dev checkout cwd",
        )

    if cwd_detected is not None:
        root = _canonical_git_root(cwd_detected)
        return WorkspaceResolution(
            root=root,
            source=WorkspaceResolutionSource.CWD_MARKERS if root else None,
        )

    root = _canonical_git_repo_root(cwd)
    return WorkspaceResolution(
        root=root,
        source=WorkspaceResolutionSource.CWD_GIT if root else None,
    )


def resolve_consumer_project_root(
    *,
    workspace: Path | None = None,
    config: dict[str, Any] | None = None,
    agent: str | None = None,
) -> Path | None:
    """Return git root for tier/MCP scoping, excluding ephemeral workspaces."""
    from cyt.common.paths import is_ephemeral_workspace_path

    resolution = resolve_consumer_workspace(workspace=workspace, config=config, agent=agent)
    root = resolution.root
    if root is None:
        return None
    if is_ephemeral_workspace_path(root):
        return None
    return root


_HOOK_SETUP_ALLOWED_SOURCES: frozenset[WorkspaceResolutionSource] = frozenset(
    {
        WorkspaceResolutionSource.EXPLICIT,
        WorkspaceResolutionSource.SHELL,
        WorkspaceResolutionSource.TERMINAL_ENV,
    },
)

_AGENT_HOOKS_HOME: dict[str, str] = {
    "cursor": "~/.cursor",
    "claude": "~/.claude",
    "codex": "~/.codex",
}


def _packaged_cyt_uv_wrapper_sources() -> tuple[Path, Path]:
    """Package-bundled templates copied into the agent hooks directory."""
    hook_dir = Path(__file__).resolve().parent
    return hook_dir / "uv.ps1", hook_dir / "uv.sh"


def agent_cyt_uv_dir(agent: str) -> Path | None:
    """``~/.<agent>/hooks/cyt`` directory for installed uv wrapper scripts."""
    agent_key = (agent or "cursor").strip().lower()
    home = _AGENT_HOOKS_HOME.get(agent_key)
    if home is None:
        return None
    return Path(home).expanduser() / "hooks" / "cyt"


def cyt_uv_wrapper_script_paths(agent: str = "cursor") -> tuple[Path, Path]:
    """Target paths for uv wrappers in ``~/.<agent>/hooks/cyt/``."""
    cyt_dir = agent_cyt_uv_dir(agent)
    if cyt_dir is None:
        return _packaged_cyt_uv_wrapper_sources()
    return cyt_dir / "uv.ps1", cyt_dir / "uv.sh"


def _remove_legacy_cyt_uv_wrappers(hooks_root: Path) -> None:
    for name in ("cyt-uv.ps1", "cyt-uv.sh"):
        legacy = hooks_root / name
        if legacy.is_file():
            legacy.unlink()


def cyt_uv_wrapper_invocation_path(agent: str = "cursor") -> Path | None:
    """Sidecar JSON beside ``uv.ps1`` / ``uv.sh`` describing how to invoke ``cyt`` via ``uv``."""
    dest_dir = agent_cyt_uv_dir(agent)
    if dest_dir is None:
        return None
    return dest_dir / CYT_UV_INVOCATION_FILENAME


def build_cyt_uv_wrapper_invocation_payload() -> dict[str, str]:
    """Return dev ``uv run --directory`` or ``uv tool run --from`` metadata for uv wrappers."""
    from cyt.hook.cli_invocation import detect_hook_cli_invocation

    invocation = detect_hook_cli_invocation()
    if invocation.repo_root is not None:
        return {
            "mode": "dev",
            "repo_root": str(invocation.repo_root.resolve()),
            "cli": CYT_CLI_APP_SCRIPT_REL,
        }
    git_root = cyt_package_git_root()
    if git_root is not None:
        return {
            "mode": "dev",
            "repo_root": str(git_root.resolve()),
            "cli": CYT_CLI_APP_SCRIPT_REL,
        }
    return {
        "mode": "tool",
        "package": CYT_UV_TOOL_PACKAGE,
        "executable": "cyt",
    }


def write_cyt_uv_wrapper_invocation(agent: str = "cursor") -> Path | None:
    """Write ``cyt-invocation.json`` next to installed uv wrapper scripts."""
    path = cyt_uv_wrapper_invocation_path(agent)
    if path is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_cyt_uv_wrapper_invocation_payload()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def ensure_cyt_mcp_dev_wrapper(agent: str = "cursor") -> Path | None:
    """Install ``~/.<agent>/hooks/cyt/mcp-dev.cmd`` when cyt hook setup runs in dev mode."""
    from cyt.platform.compat import is_windows

    if not is_windows():
        return None
    from cyt.hook.cli_invocation import detect_hook_cli_invocation

    invocation = detect_hook_cli_invocation()
    if not invocation.is_dev or invocation.repo_root is None:
        return None
    from cyt_client.hook_invocation import install_windows_cyt_mcp_dev_wrapper

    return install_windows_cyt_mcp_dev_wrapper(
        dev_repo_root=invocation.repo_root,
        agent=agent,
    )


def ensure_cyt_uv_wrapper_scripts(agent: str = "cursor") -> tuple[Path, Path]:
    """Copy packaged uv wrappers into ``~/.<agent>/hooks/cyt/``."""
    dest_dir = agent_cyt_uv_dir(agent)
    src_ps1, src_sh = _packaged_cyt_uv_wrapper_sources()
    if dest_dir is None:
        return src_ps1, src_sh
    dest_dir.mkdir(parents=True, exist_ok=True)
    _remove_legacy_cyt_uv_wrappers(dest_dir.parent)
    dest_ps1 = dest_dir / "uv.ps1"
    dest_sh = dest_dir / "uv.sh"
    shutil.copy2(src_ps1, dest_ps1)
    shutil.copy2(src_sh, dest_sh)
    try:
        dest_sh.chmod(dest_sh.stat().st_mode | 0o111)
    except OSError:
        pass
    write_cyt_uv_wrapper_invocation(agent)
    return dest_ps1, dest_sh


def hook_setup_workspace_required_message(*, agent: str = "cursor") -> str:
    ps1, sh = cyt_uv_wrapper_script_paths(agent)
    lines = [
        "cyt hook setup requires a consumer workspace. Use one of:",
        "  --workspace <full-absolute-consumer-path>",
        f"  {CYT_WORKSPACE_ENV}=<full-absolute-consumer-path>",
        "",
        "From the consumer project (wrapper sets CYT_SHELL_WORKSPACE from shell cwd):",
        "  PowerShell:",
        "    cd <consumer-repo>",
        f"    {ps1} hook cursor",
        "  Bash:",
        "    cd <consumer-repo>",
        f"    {sh} hook cursor",
    ]
    cyt_root = cyt_package_git_root()
    if cyt_root is not None:
        app = cyt_root / "src" / "cyt" / "cli" / "app.py"
        lines.extend(
            [
                "",
                "Or pass --workspace with uv --directory (dev checkout):",
                f'  uv run --directory "{cyt_root}" "{app}" '
                'hook cursor --workspace "<consumer-repo>"',
            ],
        )
    return "\n".join(lines)


def _resolve_hook_setup_from_terminal_env() -> WorkspaceResolution | None:
    """Hook setup accepts only ``CYT_WORKSPACE`` (not other terminal env aliases)."""
    raw = os.environ.get(CYT_WORKSPACE_ENV, "").strip()
    if not raw:
        return None
    root = _resolved_env_root(raw, label=f"${CYT_WORKSPACE_ENV}")
    if root is None:
        return None
    return WorkspaceResolution(
        root=root,
        source=WorkspaceResolutionSource.TERMINAL_ENV,
        detail=CYT_WORKSPACE_ENV,
    )


def _hook_setup_detection_description(resolution: WorkspaceResolution) -> str:
    source = resolution.source
    detail = resolution.detail
    if source is None:
        return "none (unresolved)"
    if source == WorkspaceResolutionSource.EXPLICIT:
        return f"explicit --workspace ({detail})"
    if source == WorkspaceResolutionSource.SHELL:
        env_key = detail or CYT_SHELL_WORKSPACE_ENV
        raw = os.environ.get(env_key, "").strip()
        return f"shell env (${env_key}={raw or detail})"
    if source == WorkspaceResolutionSource.TERMINAL_ENV:
        env_key = detail or CYT_WORKSPACE_ENV
        raw = os.environ.get(env_key, "").strip()
        return f"terminal env (${env_key}={raw})"
    if source == WorkspaceResolutionSource.HOOK_CONFIG:
        return f"hook config (${CYT_WORKSPACE_ENV})"
    if source == WorkspaceResolutionSource.CURSOR_LABEL:
        return f"active project label (CURSOR_WORKSPACE_LABEL={detail})"
    if source == WorkspaceResolutionSource.ACTIVE_REGISTRY:
        return f"recent active workspace registry (agent={detail})"
    if source == WorkspaceResolutionSource.CWD_MARKERS:
        return "process cwd (workspace markers)"
    if source == WorkspaceResolutionSource.CWD_GIT:
        return "process cwd (git root)"
    if source == WorkspaceResolutionSource.CYT_REPO_DEFAULT:
        return f"process cwd ({detail or 'cyt checkout'})"
    return str(source)


def format_hook_setup_workspace_report(resolution: WorkspaceResolution) -> str:
    """Human-readable workspace detection summary for ``cyt hook`` setup."""
    cyt_root = cyt_package_git_root()
    consumer = resolution.root
    if (
        consumer is not None
        and cyt_root is not None
        and consumer.resolve() == cyt_root.resolve()
    ):
        consumer_label = f"{consumer} (cyt package - consumer setup skipped)"
    elif consumer is not None:
        consumer_label = str(consumer)
    else:
        consumer_label = "(none)"
    return "\n".join(
        (
            "Workspace detection:",
            f"  Consumer workspace: {consumer_label}",
            f"  Detected via: {_hook_setup_detection_description(resolution)}",
        ),
    )


def print_hook_setup_workspace_report(resolution: WorkspaceResolution) -> None:
    """Print workspace detection for ``cyt hook`` setup."""
    for line in format_hook_setup_workspace_report(resolution).splitlines():
        print(line, flush=True)


def resolve_hook_setup_workspace(
    *,
    workspace: Path | None = None,
    config: dict[str, Any] | None = None,
    agent: str | None = None,
) -> WorkspaceResolution:
    """Resolve consumer workspace for ``cyt hook`` setup.

    Only ``--workspace``, shell env (``CYT_SHELL_WORKSPACE`` / ``PWD``), or
    ``CYT_WORKSPACE`` are accepted — no cwd, registry, or label heuristics.
    """
    _ = config, agent
    bootstrap_shell_workspace_env_for_hook()

    if workspace is not None:
        abs_workspace = require_absolute_workspace_dir(workspace, label="--workspace")
        root = _canonical_git_root(abs_workspace)
        return WorkspaceResolution(
            root=root,
            source=WorkspaceResolutionSource.EXPLICIT,
            detail=str(abs_workspace),
        )

    for resolver in (
        lambda: _resolve_from_shell_env(include_pwd=True),
        _resolve_hook_setup_from_terminal_env,
    ):
        resolution = resolver()
        if resolution is not None and resolution.root is not None:
            return resolution

    return WorkspaceResolution(root=None, source=None)


def _hook_setup_workspace_was_provided(
    *,
    workspace: Path | None,
    resolution: WorkspaceResolution,
) -> bool:
    if workspace is not None:
        return True
    if resolution.source in _HOOK_SETUP_ALLOWED_SOURCES:
        return True
    if os.environ.get(CYT_SHELL_WORKSPACE_ENV, "").strip():
        return True
    terminal_raw = os.environ.get(CYT_WORKSPACE_ENV, "").strip()
    if terminal_raw and not _is_template_workspace_value(terminal_raw):
        return True
    return False


def _consumer_root_from_hook_resolution(resolution: WorkspaceResolution) -> Path | None:
    """Return consumer git root when resolved and not the cyt package checkout."""
    root = resolution.root
    if root is None:
        return None
    cyt_root = cyt_package_git_root()
    if cyt_root is not None and root.resolve() == cyt_root.resolve():
        return None
    return root


def hook_setup_mcp_workspace_root(
    resolution: WorkspaceResolution,
    consumer_root: Path | None,
) -> Path | None:
    """Workspace root for MCP migration during hook setup.

    Consumer-only setup (vscode env, workspace config refresh) skips the cyt
    checkout, but workspace MCP migration should still move ``.cursor/mcp.json``
    backends into ``.agents/cyt/config/mcp/`` when that checkout is the active
    workspace.
    """
    if consumer_root is not None:
        return consumer_root
    return resolution.root


def finalize_hook_setup_consumer_root(
    resolution: WorkspaceResolution,
    *,
    workspace: Path | None = None,
    agent: str = "cursor",
) -> Path | None:
    """Apply hook-setup filters to an already-resolved workspace.

    Exits only when no ``--workspace``, ``CYT_SHELL_WORKSPACE``, or ``CYT_WORKSPACE``
    was provided. When provided but pointing at the cyt checkout or a non-git path,
    returns ``None`` and the hook wizard continues without consumer workspace setup.
    """
    if not _hook_setup_workspace_was_provided(
        workspace=workspace,
        resolution=resolution,
    ):
        raise SystemExit(hook_setup_workspace_required_message(agent=agent))
    return _consumer_root_from_hook_resolution(resolution)


def resolve_hook_setup_consumer_root(
    *,
    workspace: Path | None = None,
    agent: str | None = None,
) -> Path | None:
    """Return the consumer repo for hook workspace setup, or ``None`` to skip.

    Raises ``SystemExit`` when invoked from the cyt checkout (``uv --directory``)
    without ``--workspace``, ``CYT_SHELL_WORKSPACE`` (wrapper / ``PWD``), or ``CYT_WORKSPACE``.
    Never returns the cyt package git root itself.
    """
    try:
        resolution = resolve_hook_setup_workspace(workspace=workspace, agent=agent)
    except WorkspaceResolutionConflictError as exc:
        raise SystemExit(str(exc)) from exc

    return finalize_hook_setup_consumer_root(
        resolution,
        workspace=workspace,
        agent=agent or "cursor",
    )


_WORKSPACE_FOLDER_VALUE = "${workspaceFolder}"

# VS Code / Cursor terminal env keys — base plus per-OS overrides for Windows, Linux, macOS.
_TERMINAL_INTEGRATED_ENV_KEYS: tuple[str, ...] = (
    "terminal.integrated.env",
    "terminal.integrated.env.windows",
    "terminal.integrated.env.linux",
    "terminal.integrated.env.osx",
)


def ensure_vscode_terminal_workspace_env(workspace_root: Path) -> bool:
    """Merge ``CYT_WORKSPACE=${workspaceFolder}`` into ``.vscode/settings.json``.

    Writes the base ``terminal.integrated.env`` entry and platform-specific
    ``terminal.integrated.env.{windows,linux,osx}`` so Option C works in IDE
    integrated terminals on every OS (Cursor, Claude Code, Codex, etc.).
    """
    settings_path = workspace_root / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any]
    if settings_path.is_file():
        import json

        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raw = {}
        payload = raw if isinstance(raw, dict) else {}
    else:
        payload = {}

    changed = False
    for env_key in _TERMINAL_INTEGRATED_ENV_KEYS:
        terminal_env = payload.get(env_key)
        if not isinstance(terminal_env, dict):
            terminal_env = {}
        if terminal_env.get(CYT_WORKSPACE_ENV) == _WORKSPACE_FOLDER_VALUE:
            continue
        terminal_env[CYT_WORKSPACE_ENV] = _WORKSPACE_FOLDER_VALUE
        payload[env_key] = terminal_env
        changed = True

    if not changed:
        return False

    import json

    settings_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return True
