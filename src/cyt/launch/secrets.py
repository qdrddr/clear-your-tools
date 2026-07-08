"""Credential resolution with shell env, env files, keyring, and interactive prompt."""

from __future__ import annotations

import getpass
import json
import os
import sys
import types
from pathlib import Path
from typing import Any

from cyt.config import load_proxy_env, required_proxy_env_var_names
from cyt.launch.config import required_launch_env_var_names
from cyt.launch.upstream import AgentName
from cyt.proxy.setup_wizard import parse_env_file
from cyt.pruners.remote import PrunerSettingsCache

KEYRING_SERVICE = "cyt"  # macOS Keychain service name for the Python keyring backend
KEYRING_BLOB_ACCOUNT = "__credentials__"
CYT_SKIP_KEYRING_ENV = "CYT_SKIP_KEYRING"
_keyring_cache: dict[str, str] = {}
_keyring_blob: dict[str, str] | None = None
_keyring_blob_loaded = False
_reconciled_keyring_names: set[str] = set()
_runtime_credential_sources: dict[str, str] = {}


def _cwd_env_path() -> Path:
    from cyt.config import CWD_ENV_PATH

    return CWD_ENV_PATH


def _user_env_path() -> Path:
    from cyt.config import USER_ENV_PATH

    return USER_ENV_PATH


def _env_file_paths() -> tuple[Path, ...]:
    return (_cwd_env_path(), _user_env_path())


def env_file_source_label(path: Path) -> str:
    """Return a credential source label for *path* using its absolute path."""
    return f"env: {path.expanduser().resolve()}"


def _env_file_source(path: Path) -> str:
    return env_file_source_label(path)


def _snapshot_env() -> dict[str, str]:
    return dict(os.environ)


def _read_env_file_value(name: str) -> tuple[str | None, str | None]:
    """Return (value, source) from ``./.env`` then ``~/.config/cyt/.env``."""
    for path in _env_file_paths():
        values = parse_env_file(path)
        value = values.get(name)
        if value:
            return value, _env_file_source(path)
    return None, None


def clear_keyring_cache() -> None:
    """Reset in-process keyring caches (for tests)."""
    global _keyring_blob_loaded
    _keyring_cache.clear()
    _reconciled_keyring_names.clear()
    _keyring_blob_loaded = False
    globals()["_keyring_blob"] = None
    _runtime_credential_sources.clear()


def _skip_keyring_resolution() -> bool:
    return os.environ.get(CYT_SKIP_KEYRING_ENV) == "1"


def keyring_backend_available() -> bool:
    """Return True when the OS keyring backend is installed and usable."""
    try:
        import keyring
    except ImportError:
        return False
    try:
        backend = keyring.get_keyring()
    except Exception:
        return False
    return backend.__class__.__module__ != "keyring.backends.fail"


def _import_keyring() -> types.ModuleType | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _decode_keyring_blob(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, str] = {}
    for key, value in payload.items():
        if isinstance(key, str) and value is not None and str(value):
            result[key] = str(value)
    return result


def _encode_keyring_blob(blob: dict[str, str]) -> str:
    return json.dumps(blob, sort_keys=True, separators=(",", ":"))


def _ensure_keyring_blob_loaded() -> dict[str, str]:
    global _keyring_blob_loaded
    if _keyring_blob_loaded and _keyring_blob is not None:
        return _keyring_blob

    keyring = _import_keyring()
    blob: dict[str, str] = {}
    if keyring is not None:
        try:
            raw = keyring.get_password(KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT)
            blob = _decode_keyring_blob(raw)
        except Exception:
            blob = {}

    globals()["_keyring_blob"] = blob
    _keyring_blob_loaded = True
    return blob


def _mark_keyring_reconciled(name: str, value: str | None) -> None:
    _reconciled_keyring_names.add(name)
    _keyring_cache[name] = value or ""


def _reconciled_keyring_value(name: str) -> str | None:
    if name not in _reconciled_keyring_names:
        return None
    return _keyring_cache.get(name) or None


def _save_keyring_blob(blob: dict[str, str]) -> bool:
    keyring = _import_keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT, _encode_keyring_blob(blob))
    except Exception:
        return False
    globals()["_keyring_blob"] = dict(blob)
    for name, value in blob.items():
        _mark_keyring_reconciled(name, value)
    return True


def _read_legacy_keyring(name: str) -> str | None:
    keyring = _import_keyring()
    if keyring is None:
        return None
    try:
        raw = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None
    if raw:
        return str(raw)
    return None


def preload_keyring_credentials(names: list[str]) -> None:
    """Load the keyring credential blob once and warm the in-process cache."""
    if not names or _skip_keyring_resolution():
        return
    _ensure_keyring_blob_loaded()
    for name in dict.fromkeys(names):
        if name in _reconciled_keyring_names:
            continue
        _read_keyring(name)


def _read_keyring(name: str) -> str | None:
    if _skip_keyring_resolution():
        return None
    if name in _reconciled_keyring_names:
        return _reconciled_keyring_value(name)

    blob = _ensure_keyring_blob_loaded()
    legacy = _read_legacy_keyring(name)
    blob_value = blob.get(name)

    # Prefer the legacy per-key slot when it disagrees with the blob. Users who
    # updated credentials via the old keychain account still have the real key
    # there while the migrated blob may retain a stale placeholder.
    if legacy and blob_value and legacy != blob_value:
        blob[name] = legacy
        _save_keyring_blob(blob)
        _mark_keyring_reconciled(name, legacy)
        return legacy

    if blob_value:
        _mark_keyring_reconciled(name, blob_value)
        return blob_value

    if legacy:
        blob[name] = legacy
        _mark_keyring_reconciled(name, legacy)
        _save_keyring_blob(blob)
        return legacy

    _mark_keyring_reconciled(name, None)
    return None


def _write_keyring(name: str, value: str) -> bool:
    blob = _ensure_keyring_blob_loaded()
    blob[name] = value
    if not _save_keyring_blob(blob):
        return False
    _mark_keyring_reconciled(name, value)
    return True


def credentials_available_in_keyring(names: list[str]) -> bool:
    """Return True when every *names* entry resolves from the OS keyring."""
    if not names:
        return True
    preload_keyring_credentials(names)
    return all(_read_keyring(name) for name in names)


def _process_env_credential(name: str) -> tuple[str | None, str | None]:
    """Return credentials already present in the process environment."""
    if value := os.environ.get(name):
        return value, "env: process"
    return None, None


def _remember_runtime_credential(name: str, source: str) -> None:
    _runtime_credential_sources[name] = source


def _resolve_keyring_credential(name: str) -> tuple[str | None, str | None]:
    if name in _reconciled_keyring_names:
        if value := _reconciled_keyring_value(name):
            os.environ[name] = value
            _remember_runtime_credential(name, "keyring")
            return value, "keyring"
        return None, None
    if value := _read_keyring(name):
        os.environ[name] = value
        _remember_runtime_credential(name, "keyring")
        return value, "keyring"
    return None, None


def _resolve_file_credential(name: str) -> tuple[str | None, str | None]:
    file_value, file_source = _read_env_file_value(name)
    if file_value and file_source:
        os.environ[name] = file_value
        _remember_runtime_credential(name, file_source)
        return file_value, file_source
    return None, None


def _resolve_terminal_credential(
    name: str,
    *,
    before_env: dict[str, str],
) -> tuple[str | None, str | None]:
    """Return credentials exported in the shell; terminal values beat ``.env`` and keyring."""
    from cyt.config import process_env_before_dotenv

    pre_dotenv = process_env_before_dotenv()
    if value := pre_dotenv.get(name):
        file_value, file_source = _read_env_file_value(name)
        if file_value and file_value == value and file_source:
            return value, file_source
        return value, "env: shell"

    if name in _runtime_credential_sources:
        return None, None

    # Values loaded from ``.env`` files at import time are already in
    # ``before_env`` but are not shell exports; let file resolution label them.
    if value := before_env.get(name):
        file_value, _ = _read_env_file_value(name)
        if file_value and file_value == value:
            return None, None
    return None, None


def _resolve_prompt_credential(name: str) -> tuple[str | None, str | None]:
    if not sys.stdin.isatty():
        return None, None

    value = getpass.getpass(f"{name}: ")
    if not value:
        return None, None

    os.environ[name] = value
    if _write_keyring(name, value):
        _remember_runtime_credential(name, "keyring")
        return value, "keyring"
    _remember_runtime_credential(name, "prompt")
    return value, "prompt"


def resolve_shell_or_file_credential(
    name: str,
    *,
    before_env: dict[str, str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve *name* from shell exports and ``.env`` files only (no keyring or prompt)."""
    resolved_before_env = before_env if before_env is not None else _snapshot_env()
    terminal_value = _resolve_terminal_credential(name, before_env=resolved_before_env)
    if terminal_value[0] and terminal_value[1]:
        return terminal_value
    return _resolve_file_credential(name)


def resolve_keyring_or_prompt_credential(
    name: str,
    *,
    allow_prompt: bool = True,
) -> tuple[str | None, str | None]:
    """Resolve *name* from the OS keyring, then an interactive terminal prompt."""
    keyring_value = _resolve_keyring_credential(name)
    if keyring_value[0] and keyring_value[1]:
        return keyring_value
    if not allow_prompt:
        return None, None
    return _resolve_prompt_credential(name)


def resolve_credential(
    name: str,
    *,
    before_env: dict[str, str] | None = None,
    allow_prompt: bool = True,
) -> tuple[str | None, str | None]:
    """Return (value, source) for *name* without printing secrets.

    Resolution order:
    1. Shell / terminal environment (exports take precedence over ``.env`` files)
    2. ``./.env``, then ``~/.config/cyt/.env``
    3. OS keyring (``cyt`` service, single blob entry)
    4. Interactive prompt; saved to keyring when possible, else current process env

    When ``CYT_SKIP_KEYRING=1`` is set, keyring access is skipped and existing
    process environment values are preferred (used by proxy children spawned
    from ``cyt launch``).
    """
    if _skip_keyring_resolution():
        value, source = _process_env_credential(name)
        if value and source:
            return value, source
        return None, None

    if cached_source := _runtime_credential_sources.get(name):
        if value := os.environ.get(name):
            return value, cached_source

    resolved_before_env = before_env if before_env is not None else _snapshot_env()

    terminal_value = _resolve_terminal_credential(name, before_env=resolved_before_env)
    if terminal_value[0] and terminal_value[1]:
        return terminal_value

    file_value = _resolve_file_credential(name)
    if file_value[0] and file_value[1]:
        return file_value

    keyring_value = _resolve_keyring_credential(name)
    if keyring_value[0] and keyring_value[1]:
        return keyring_value

    if not allow_prompt:
        return None, None

    return _resolve_prompt_credential(name)


def required_env_var_names(
    config: dict[str, Any],
    *,
    agent: AgentName | None,
    endpoint: str | None = None,
) -> list[str]:
    """Return env var names required for proxy or launch."""
    if agent is None:
        return required_proxy_env_var_names(config)
    return required_launch_env_var_names(config, agent, endpoint=endpoint)


def ensure_runtime_credentials(
    config: dict[str, Any],
    *,
    agent: AgentName | None,
    credential_sources: dict[str, str],
    endpoint: str | None = None,
) -> None:
    """Ensure required credentials are available; populate *credential_sources*."""
    names = required_env_var_names(config, agent=agent, endpoint=endpoint)
    ensure_named_credentials(names, credential_sources=credential_sources)


def resolve_hook_daemon_child_env(
    config: dict[str, Any],
    *,
    allow_prompt: bool = False,
    require_all: bool = True,
) -> dict[str, str]:
    """Resolve tool/skills pruner API keys for a hook daemon child process."""
    from cyt.config import required_tools_hook_env_var_names

    names = list(
        dict.fromkeys(
            [
                *required_proxy_env_var_names(config),
                *required_tools_hook_env_var_names(config),
            ],
        ),
    )
    if not names:
        return {}
    credential_sources: dict[str, str] = {}
    ensure_named_credentials(
        names,
        credential_sources=credential_sources,
        allow_prompt=allow_prompt,
        require_all=require_all,
    )
    return {name: value for name in credential_sources if (value := os.environ.get(name))}


def ensure_proxy_pipeline_credentials(
    config: dict[str, Any],
    *,
    credential_sources: dict[str, str],
) -> PrunerSettingsCache:
    """Resolve all tool/skills pruner API keys before the proxy accepts traffic."""

    ensure_runtime_credentials(config, agent=None, credential_sources=credential_sources)
    verify_pipeline_credentials_resolved(config)
    return build_pruner_settings_cache(config)


def verify_pipeline_credentials_resolved(config: dict[str, Any]) -> None:
    """Fail fast when configured pipeline keys are missing from the process environment."""
    from cyt.config import require_proxy_env

    require_proxy_env(config)


def build_pruner_settings_cache(config: dict[str, Any]) -> PrunerSettingsCache:
    """Construct configured remote pruner clients at startup (never on first request)."""
    from cyt.config import (
        pruning_pipeline_from_config,
        skills_enabled,
        skills_pipeline,
    )
    from cyt.pruners.llm import llm_pruning_settings
    from cyt.pruners.rerank import rerank_pruning_settings

    cache = PrunerSettingsCache()
    for stage in pruning_pipeline_from_config(config):
        if stage == "rerank":
            cache.rerank = rerank_pruning_settings(config)
        elif stage == "llm":
            cache.llm = llm_pruning_settings(config)

    if skills_enabled(config):
        pipeline = skills_pipeline(config).strip().lower()
        if pipeline == "rerank" and cache.rerank is None:
            cache.rerank = rerank_pruning_settings(config)
        elif pipeline == "llm" and cache.llm is None:
            cache.llm = llm_pruning_settings(config)

    return cache


def warm_pipeline_pruner_settings(config: dict[str, Any]) -> PrunerSettingsCache:
    """Construct configured remote pruner clients at startup (never on first request)."""
    return build_pruner_settings_cache(config)


def inspect_named_credentials(
    names: list[str],
    *,
    allow_prompt: bool = False,
) -> list[tuple[str, str | None]]:
    """Return ``(name, source)`` pairs; source is ``None`` when unresolved."""
    before = _snapshot_env()
    load_proxy_env()
    preload_keyring_credentials(names)
    results: list[tuple[str, str | None]] = []
    for name in names:
        value, source = resolve_credential(
            name,
            before_env=before,
            allow_prompt=allow_prompt,
        )
        results.append((name, source if value else None))
    return results


def ensure_wizard_credentials(
    names: list[str],
    *,
    env_fallback_path: Path | None = None,
) -> dict[str, str]:
    """Ensure *names* exist; persist prompted values to *env_fallback_path* when keyring fails."""
    from cyt.proxy.setup_wizard import write_env_file

    if env_fallback_path is None:
        env_fallback_path = _user_env_path()

    before = _snapshot_env()
    load_proxy_env()
    preload_keyring_credentials(names)
    sources: dict[str, str] = {}
    env_updates: dict[str, str] = {}
    missing: list[str] = []
    allow_prompt = sys.stdin.isatty()

    for name in names:
        value, source = resolve_credential(
            name,
            before_env=before,
            allow_prompt=allow_prompt,
        )
        if not value or not source:
            missing.append(name)
            continue
        if source == "prompt":
            env_updates[name] = value
            sources[name] = _env_file_source(env_fallback_path)
            continue
        sources[name] = source

    if env_updates:
        write_env_file(env_fallback_path, env_updates)
        print(f"Wrote {env_fallback_path.expanduser()}")

    if missing:
        vars_block = "\n".join(f"\t{name}" for name in missing)
        env_locations = "\n".join(f"\t{p}" for p in (_cwd_env_path(), _user_env_path()))
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Export them in the shell or define them in\n{env_locations}\n"
            "Or run interactively to store them in the keyring.",
        )

    return sources


def ensure_named_credentials(
    names: list[str],
    *,
    credential_sources: dict[str, str] | None = None,
    allow_prompt: bool = True,
    require_all: bool = True,
) -> list[str]:
    """Ensure *names* are available using the standard credential resolution order.

    Returns unresolved variable names. Raises ``SystemExit`` when *require_all* is
    true and any name remains missing after resolution.
    """
    before = _snapshot_env()
    load_proxy_env()
    preload_keyring_credentials(names)

    missing: list[str] = []
    for name in names:
        value, source = resolve_credential(
            name,
            before_env=before,
            allow_prompt=allow_prompt,
        )
        if value and source:
            os.environ[name] = value
            if credential_sources is not None:
                credential_sources[name] = source
        else:
            missing.append(name)

    if missing and require_all:
        vars_block = "\n".join(f"\t{name}" for name in missing)
        env_locations = "\n".join(f"\t{p}" for p in (_cwd_env_path(), _user_env_path()))
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Export them in the shell or define them in\n{env_locations}\n"
            "Or run interactively to store them in the keyring.",
        )
    return missing
