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
from cyt.proxy.setup import parse_env_file

KEYRING_SERVICE = "cyt"
KEYRING_BLOB_ACCOUNT = "__credentials__"
CYT_SKIP_KEYRING_ENV = "CYT_SKIP_KEYRING"

_keyring_cache: dict[str, str] = {}
_keyring_blob: dict[str, str] | None = None
_keyring_blob_loaded = False


def _cwd_env_path() -> Path:
    from cyt.config import CWD_ENV_PATH

    return CWD_ENV_PATH


def _user_env_path() -> Path:
    from cyt.config import USER_ENV_PATH

    return USER_ENV_PATH


def _env_file_source(path: Path) -> str:
    expanded = path.expanduser()
    cwd_env = _cwd_env_path()
    user_env = _user_env_path()
    if expanded == cwd_env:
        return "env: ./.env"
    if expanded == user_env:
        return "env: ~/.config/cyt/.env"
    return f"env: {expanded}"


def _snapshot_env() -> dict[str, str]:
    return dict(os.environ)


def _read_env_file_value(name: str) -> tuple[str | None, str | None]:
    """Return (value, source) from ``./.env`` then ``~/.config/cyt/.env``."""
    for path in (_cwd_env_path(), _user_env_path()):
        values = parse_env_file(path)
        value = values.get(name)
        if value:
            return value, _env_file_source(path)
    return None, None


def clear_keyring_cache() -> None:
    """Reset in-process keyring caches (for tests)."""
    global _keyring_blob_loaded
    _keyring_cache.clear()
    _keyring_blob_loaded = False
    globals()["_keyring_blob"] = None


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
    _keyring_cache.update(blob)
    return blob


def _save_keyring_blob(blob: dict[str, str]) -> bool:
    keyring = _import_keyring()
    if keyring is None:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_BLOB_ACCOUNT, _encode_keyring_blob(blob))
    except Exception:
        return False
    globals()["_keyring_blob"] = dict(blob)
    _keyring_cache.update(blob)
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
    for name in names:
        if name in _keyring_cache:
            continue
        if _keyring_blob is not None and (value := _keyring_blob.get(name)):
            _keyring_cache[name] = value


def _read_keyring(name: str) -> str | None:
    if _skip_keyring_resolution():
        return None
    if name in _keyring_cache:
        cached = _keyring_cache[name]
        return cached or None

    blob = _ensure_keyring_blob_loaded()
    if value := blob.get(name):
        _keyring_cache[name] = value
        return value

    if legacy := _read_legacy_keyring(name):
        blob[name] = legacy
        _keyring_cache[name] = legacy
        _save_keyring_blob(blob)
        return legacy

    _keyring_cache[name] = ""
    return None


def _write_keyring(name: str, value: str) -> bool:
    blob = _ensure_keyring_blob_loaded()
    blob[name] = value
    if not _save_keyring_blob(blob):
        return False
    _keyring_cache[name] = value
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


def _resolve_keyring_credential(name: str) -> tuple[str | None, str | None]:
    if value := _read_keyring(name):
        os.environ[name] = value
        return value, "keyring"
    return None, None


def _resolve_file_credential(name: str) -> tuple[str | None, str | None]:
    file_value, file_source = _read_env_file_value(name)
    if file_value and file_source:
        os.environ[name] = file_value
        return file_value, file_source
    return None, None


def _resolve_terminal_credential(name: str) -> tuple[str | None, str | None]:
    """Return credentials exported in the shell; terminal values beat ``.env`` and keyring."""
    from cyt.config import process_env_before_dotenv

    if value := process_env_before_dotenv().get(name):
        if value:
            return value, "env: shell"

    if not (value := os.environ.get(name)):
        return None, None

    file_value, _ = _read_env_file_value(name)
    if not file_value:
        return value, "env: shell"
    if value != file_value:
        return value, "env: shell"
    return None, None


def _resolve_prompt_credential(name: str) -> tuple[str | None, str | None]:
    if not sys.stdin.isatty():
        return None, None

    value = getpass.getpass(f"{name}: ")
    if not value:
        return None, None

    os.environ[name] = value
    if _write_keyring(name, value):
        return value, "keyring"
    return value, "prompt"


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
    del before_env  # terminal resolution uses ``process_env_before_dotenv()``

    if _skip_keyring_resolution():
        value, source = _process_env_credential(name)
        if value and source:
            return value, source
        return None, None

    terminal_value = _resolve_terminal_credential(name)
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


def ensure_proxy_pipeline_credentials(
    config: dict[str, Any],
    *,
    credential_sources: dict[str, str],
) -> None:
    """Resolve all tool/skills pruner API keys before the proxy accepts traffic."""
    ensure_runtime_credentials(config, agent=None, credential_sources=credential_sources)
    verify_pipeline_credentials_resolved(config)
    warm_pipeline_pruner_settings(config)


def verify_pipeline_credentials_resolved(config: dict[str, Any]) -> None:
    """Fail fast when configured pipeline keys are missing from the process environment."""
    from cyt.config import require_proxy_env

    require_proxy_env(config)


def warm_pipeline_pruner_settings(config: dict[str, Any]) -> None:
    """Construct configured remote pruner clients at startup (never on first request)."""
    from cyt.config import (
        pruning_pipeline_from_config,
        skills_enabled,
        skills_pipeline,
    )

    for stage in pruning_pipeline_from_config(config):
        if stage == "rerank":
            from cyt.pruners.rerank import rerank_pruning_settings

            rerank_pruning_settings(config)
        elif stage == "llm":
            from cyt.pruners.llm import llm_pruning_settings

            llm_pruning_settings(config)

    if skills_enabled(config):
        pipeline = skills_pipeline(config).strip().lower()
        if pipeline == "rerank":
            from cyt.pruners.rerank import rerank_pruning_settings

            rerank_pruning_settings(config)
        elif pipeline == "llm":
            from cyt.pruners.llm import llm_pruning_settings

            llm_pruning_settings(config)


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
    from cyt.proxy.setup import write_env_file

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
) -> None:
    """Ensure *names* are available using the standard credential resolution order."""
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

    if missing:
        vars_block = "\n".join(f"\t{name}" for name in missing)
        env_locations = "\n".join(f"\t{p}" for p in (_cwd_env_path(), _user_env_path()))
        raise SystemExit(
            f"Required environment variable(s) not set:\n{vars_block}\n"
            f"Export them in the shell or define them in\n{env_locations}\n"
            "Or run interactively to store them in the keyring.",
        )
