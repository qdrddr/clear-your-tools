"""Credential resolution with keyring, env files, shell env, and interactive prompt."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path
from typing import Any

from cyt.config import load_proxy_env, required_proxy_env_var_names
from cyt.launch.config import required_launch_env_var_names
from cyt.launch.upstream import AgentName
from cyt.proxy.setup import parse_env_file

KEYRING_SERVICE = "cyt"


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


def keyring_backend_available() -> bool:
    """Return True when the OS keyring backend is installed and can store secrets."""
    try:
        import keyring
    except ImportError:
        return False
    try:
        probe_key = "__cyt_keyring_probe__"
        keyring.set_password(KEYRING_SERVICE, probe_key, "ok")
        value = keyring.get_password(KEYRING_SERVICE, probe_key)
        try:
            keyring.delete_password(KEYRING_SERVICE, probe_key)
        except Exception:
            pass
        return value == "ok"
    except Exception:
        return False


def credentials_available_in_keyring(names: list[str]) -> bool:
    """Return True when every *names* entry resolves from the OS keyring."""
    if not names:
        return True
    return all(_read_keyring(name) for name in names)


def _read_keyring(name: str) -> str | None:
    try:
        import keyring
    except ImportError:
        return None
    try:
        value = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None
    if value:
        return value
    return None


def _write_keyring(name: str, value: str) -> bool:
    try:
        import keyring
    except ImportError:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, name, value)
    except Exception:
        return False
    return True


def resolve_credential(
    name: str,
    *,
    before_env: dict[str, str],
    allow_prompt: bool = True,
) -> tuple[str | None, str | None]:
    """Return (value, source) for *name* without printing secrets.

    Resolution order:
    1. OS keyring (``cyt`` service)
    2. ``./.env``, then ``~/.config/cyt/.env``
    3. Shell environment captured before ``load_proxy_env()``
    4. Interactive prompt; saved to keyring when possible, else current process env
    """
    if value := _read_keyring(name):
        os.environ[name] = value
        return value, "keyring"

    file_value, file_source = _read_env_file_value(name)
    if file_value and file_source:
        os.environ[name] = file_value
        return file_value, file_source

    if shell_value := before_env.get(name):
        if shell_value:
            return shell_value, "env: shell"

    if not allow_prompt:
        return None, None

    if not sys.stdin.isatty():
        return None, None

    value = getpass.getpass(f"{name}: ")
    if not value:
        return None, None

    os.environ[name] = value
    if _write_keyring(name, value):
        return value, "keyring"
    return value, "prompt"


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


def inspect_named_credentials(
    names: list[str],
    *,
    allow_prompt: bool = False,
) -> list[tuple[str, str | None]]:
    """Return ``(name, source)`` pairs; source is ``None`` when unresolved."""
    before = _snapshot_env()
    load_proxy_env()
    results: list[tuple[str, str | None]] = []
    for name in names:
        value, source = resolve_credential(
            name,
            before_env=before,
            allow_prompt=allow_prompt,
        )
        results.append((name, source if value else None))
    return results


def ensure_named_credentials(
    names: list[str],
    *,
    credential_sources: dict[str, str] | None = None,
    allow_prompt: bool = True,
) -> None:
    """Ensure *names* are available using the standard credential resolution order."""
    before = _snapshot_env()
    load_proxy_env()

    missing: list[str] = []
    for name in names:
        value, source = resolve_credential(
            name,
            before_env=before,
            allow_prompt=allow_prompt,
        )
        if value and source:
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
