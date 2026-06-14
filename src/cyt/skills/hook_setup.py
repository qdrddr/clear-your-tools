"""Interactive wizard for installing `cyt hook --stdin` agent hooks."""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

from cyt.config import (
    USER_ENV_PATH,
    load_config,
    load_user_config_overlay,
    required_proxy_env_var_names,
    resolve_setup_config_path,
    save_user_config,
    skills_enabled,
)
from cyt.proxy.setup import _prompt, _prompt_yes_no, parse_path_list

CLAUDE_SETTINGS_PATH = Path("~/.claude/settings.json")
CODEX_HOOKS_PATH = Path("~/.codex/hooks.json")
CLAUDE_SKILLS_DIR = Path("~/.claude/skills")
CODEX_SKILLS_DIR = Path("~/.codex/skills")
HOOK_EVENT_NAME = "UserPromptSubmit"
CYT_HOOK_COMMAND_PREFIX = "cyt hook"


def merge_skills_directory_lists(
    existing: list[str],
    new_dirs: list[str],
) -> tuple[list[str], bool]:
    """Append skill directory paths from *new_dirs* when not already present."""
    merged = [str(path) for path in existing if str(path).strip()]
    seen = {str(Path(path).expanduser()) for path in merged}
    changed = False
    for raw in new_dirs:
        text = str(raw).strip()
        if not text:
            continue
        expanded = str(Path(text).expanduser())
        if expanded in seen:
            continue
        merged.append(text)
        seen.add(expanded)
        changed = True
    return merged, changed


def default_hook_skills_directories(
    skills_cfg: dict[str, Any],
    *,
    include_claude: bool,
    include_codex: bool,
) -> list[str]:
    raw = skills_cfg.get("directories")
    if isinstance(raw, list) and raw:
        return [str(path) for path in raw if str(path).strip()]

    defaults: list[str] = []
    if include_claude:
        defaults.append(str(CLAUDE_SKILLS_DIR))
    if include_codex:
        defaults.append(str(CODEX_SKILLS_DIR))
    return defaults


def _prompt_hook_skills_directories(
    skills_cfg: dict[str, Any],
    *,
    include_claude: bool,
    include_codex: bool,
) -> list[str]:
    default_dirs = default_hook_skills_directories(
        skills_cfg,
        include_claude=include_claude,
        include_codex=include_codex,
    )
    default_str = ", ".join(default_dirs)
    while True:
        raw = _prompt("Skills directories (comma-separated paths)", default_str)
        parsed = parse_path_list(raw)
        if parsed is not None:
            return parsed
        if default_str:
            return default_dirs
        print("Enter at least one directory path.", file=sys.stderr)


def _ensure_skill_directories_exist(directories: list[str]) -> None:
    for raw in directories:
        path = Path(raw).expanduser()
        path.mkdir(parents=True, exist_ok=True)


def build_hook_skills_config_overlay(
    existing_skills: dict[str, Any],
    directories: list[str],
) -> dict[str, Any] | None:
    """Build a skills overlay for hook mode, or ``None`` when no config write is needed."""
    existing_dirs = existing_skills.get("directories")
    if not isinstance(existing_dirs, list):
        existing_dirs = []

    merged_dirs, dirs_changed = merge_skills_directory_lists(existing_dirs, directories)
    enabled_ok = existing_skills.get("enabled") is True
    inject_via = str(existing_skills.get("inject_via", "")).strip().lower()
    inject_via_ok = inject_via == "hook"

    if enabled_ok and inject_via_ok and not dirs_changed:
        return None

    return {
        "skills": {
            "enabled": True,
            "inject_via": "hook",
            "directories": merged_dirs,
        },
    }


def _save_hook_skills_directories(
    config_path: Path,
    directories: list[str],
    *,
    user_overlay: dict[str, Any],
) -> bool:
    skills_cfg = user_overlay.get("skills")
    existing_skills = skills_cfg if isinstance(skills_cfg, dict) else {}
    overlay = build_hook_skills_config_overlay(existing_skills, directories)
    if overlay is None:
        return False
    return save_user_config(config_path, overlay, apply_bundled_sections=False)


def _configure_hook_skills_directories(
    *,
    config_path: Path,
    include_claude: bool,
    include_codex: bool,
) -> None:
    user_overlay = load_user_config_overlay(config_path)
    skills_cfg = user_overlay.get("skills")
    skills_cfg = skills_cfg if isinstance(skills_cfg, dict) else {}

    print("\n--- Skills directories ---")
    directories = _prompt_hook_skills_directories(
        skills_cfg,
        include_claude=include_claude,
        include_codex=include_codex,
    )
    _ensure_skill_directories_exist(directories)
    if _save_hook_skills_directories(config_path, directories, user_overlay=user_overlay):
        print(f"Updated skills config in {config_path} (enabled, inject_via: hook, directories)")
    else:
        print(f"Skills config already set for hook mode in {config_path}")


def cyt_hook_entry(*, debug: bool = False) -> dict[str, Any]:
    command = "cyt hook --stdin"
    if debug:
        command += " --debug"
    return {"type": "command", "command": command, "timeout": 30}


def _is_cyt_hook_command(command: object) -> bool:
    if not isinstance(command, str):
        return False
    normalized = command.strip()
    return normalized.startswith((CYT_HOOK_COMMAND_PREFIX, "cyt skills"))


def _iter_hook_commands(hooks_section: dict[str, Any]) -> Iterator[object]:
    for event_entries in hooks_section.values():
        if not isinstance(event_entries, list):
            continue
        for wrapper in event_entries:
            if not isinstance(wrapper, dict):
                continue
            inner = wrapper.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if isinstance(hook, dict):
                    yield hook.get("command")


def cyt_hook_command_exists(hooks_section: object) -> bool:
    """Return True when a CYT hook command is already configured."""
    if not isinstance(hooks_section, dict):
        return False
    section = cast(dict[str, Any], hooks_section)
    return any(_is_cyt_hook_command(command) for command in _iter_hook_commands(section))


def merge_cyt_hook(
    hooks_section: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Append *entry* to hooks unless an equivalent CYT hook already exists."""
    merged = copy.deepcopy(hooks_section) if hooks_section else {}
    if cyt_hook_command_exists(merged):
        return merged, False

    wrappers = merged.setdefault(HOOK_EVENT_NAME, [])
    if not isinstance(wrappers, list):
        wrappers = []
        merged[HOOK_EVENT_NAME] = wrappers

    target: dict[str, Any] | None = None
    for wrapper in wrappers:
        if isinstance(wrapper, dict) and isinstance(wrapper.get("hooks"), list):
            target = wrapper
            break
    if target is None:
        target = {"hooks": []}
        wrappers.append(target)

    inner = target["hooks"]
    assert isinstance(inner, list)
    command = entry.get("command")
    for hook in inner:
        if isinstance(hook, dict) and hook.get("command") == command:
            return merged, False
    inner.append(copy.deepcopy(entry))
    return merged, True


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _write_json_object(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def merge_hooks_into_file(
    path: Path,
    entry: dict[str, Any],
    *,
    hooks_key: str = "hooks",
) -> bool:
    """Merge CYT hook into *path*; return True when the file changed."""
    existing = _load_json_object(path)
    hooks_section = existing.get(hooks_key)
    if not isinstance(hooks_section, dict):
        hooks_section = {}
    merged_hooks, changed = merge_cyt_hook(hooks_section, entry)
    if not changed:
        return False
    existing[hooks_key] = merged_hooks
    _write_json_object(path, existing)
    return True


def _agent_config_ready(path: Path) -> bool:
    resolved = path.expanduser()
    if resolved.is_file():
        return True
    return resolved.parent.is_dir()


def _agent_config_path(path: Path) -> Path:
    return path.expanduser()


def _ensure_hook_credentials(config: dict[str, Any]) -> None:
    from cyt.launch.secrets import ensure_wizard_credentials, inspect_named_credentials

    names = required_proxy_env_var_names(config)
    if not names:
        print("Hook credentials: none required for the current pipeline.")
        return

    print("Checking required API keys:")
    before_sources = dict(inspect_named_credentials(names, allow_prompt=False))
    for name in names:
        source = before_sources.get(name)
        if source:
            print(f"  {name}: {source}")
        else:
            print(f"  {name}: missing")

    missing_before = [name for name in names if not before_sources.get(name)]
    if not missing_before and all(
        source == "keyring" for source in before_sources.values() if source
    ):
        print("All required keys are already available.")
        return

    if missing_before and not sys.stdin.isatty():
        vars_block = "\n".join(f"\t{name}" for name in missing_before)
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Run `cyt hook` interactively or define them in {USER_ENV_PATH}.",
        )

    sources = ensure_wizard_credentials(names, env_fallback_path=USER_ENV_PATH)
    persisted = [
        name for name in names if sources.get(name) and sources[name] != before_sources.get(name)
    ]
    if persisted:
        print("Updated credentials:")
        for name in persisted:
            print(f"  {name}: {sources[name]}")


def run_hook_setup(*, config_path: Path | None = None) -> None:
    """Install CYT agent hooks and ensure runtime credentials."""
    resolved_config_path = resolve_setup_config_path(config_path)
    config = load_config(config_path)
    print("CYT hook setup\n")

    if not skills_enabled(config):
        print(
            "Note: skills.enabled is false in config; hooks will not inject skills until enabled.",
            file=sys.stderr,
        )

    _ensure_hook_credentials(config)

    include_claude = _agent_config_ready(CLAUDE_SETTINGS_PATH)
    include_codex = _agent_config_ready(CODEX_HOOKS_PATH)

    targets: list[tuple[str, Path]] = []
    if include_claude:
        targets.append(("Claude Code", _agent_config_path(CLAUDE_SETTINGS_PATH)))
    else:
        print(f"Skipping Claude ({CLAUDE_SETTINGS_PATH}): config file not found.")

    if include_codex:
        targets.append(("Codex", _agent_config_path(CODEX_HOOKS_PATH)))
    else:
        print(f"Skipping Codex ({CODEX_HOOKS_PATH}): config file not found.")

    if not targets:
        raise SystemExit(
            "No agent config files found; create ~/.claude/settings.json or ~/.codex/hooks.json first.",
        )

    _configure_hook_skills_directories(
        config_path=resolved_config_path,
        include_claude=include_claude,
        include_codex=include_codex,
    )

    debug = _prompt_yes_no("Enable hook debug logging (--debug)?", default_yes=False)
    entry = cyt_hook_entry(debug=debug)

    any_changed = False
    for label, path in targets:
        if cyt_hook_command_exists(_load_json_object(path).get("hooks")):
            print(f"{label}: CYT hook already configured in {path}")
            continue
        if merge_hooks_into_file(path, entry):
            print(f"{label}: added CYT hook to {path}")
            any_changed = True
        else:
            print(f"{label}: CYT hook already configured in {path}")

    if any_changed:
        print("\nRestart your agent so hook changes take effect.")
    else:
        print("\nNo hook files were modified.")
