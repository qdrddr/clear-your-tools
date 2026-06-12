"""Codex launcher through the CYT reverse proxy."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cyt.launch.config import codex_env_key_name

CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
PROVIDER_NAME = "cyt"
MANAGED_START = "# cyt-launch-managed-start"
MANAGED_END = "# cyt-launch-managed-end"

_MANAGED_OVERRIDE_PATTERNS = (
    re.compile(r"^model_provider\s*="),
    re.compile(r"^model_providers\.cyt\."),
    re.compile(r"^\[model_providers\.cyt\]"),
)


def find_codex() -> str:
    """Locate the Codex CLI binary."""
    if found := shutil.which("codex"):
        return found
    raise SystemExit("Codex CLI not found. Install it or add `codex` to PATH.")


def _provider_block(*, base_url: str, env_key: str) -> str:
    return (
        f"{MANAGED_START}\n"
        f'model_provider = "{PROVIDER_NAME}"\n'
        f"[model_providers.{PROVIDER_NAME}]\n"
        f'name = "Clear-Your-Tools"\n'
        f'env_key = "{env_key}"\n'
        f'base_url = "{base_url.rstrip("/")}/"\n'
        f'wire_api = "responses"\n'
        f"{MANAGED_END}\n"
    )


def _managed_block_re() -> re.Pattern[str]:
    return re.compile(
        rf"{re.escape(MANAGED_START)}.*?{re.escape(MANAGED_END)}\n?",
        re.DOTALL,
    )


def _extract_managed_block(text: str) -> str | None:
    match = _managed_block_re().search(text)
    if match is None:
        return None
    return match.group(0)


def _desired_provider_block(*, port: int, endpoint: str, env_key: str) -> str:
    base_url = f"http://127.0.0.1:{port}/{endpoint}/v1"
    return _provider_block(base_url=base_url, env_key=env_key)


def read_codex_config() -> str:
    if CODEX_CONFIG_PATH.is_file():
        return CODEX_CONFIG_PATH.read_text(encoding="utf-8")
    return ""


def write_codex_config(text: str) -> None:
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(text, encoding="utf-8")


def _sync_managed_provider_block(
    *,
    port: int,
    endpoint: str,
    env_key: str,
) -> bool:
    """Ensure the launch-managed block matches desired settings.

    Returns True when ``~/.codex/config.toml`` was updated.
    """
    desired = _desired_provider_block(port=port, endpoint=endpoint, env_key=env_key)
    current = read_codex_config()
    existing = _extract_managed_block(current)
    if existing == desired:
        return False
    if existing is not None:
        write_codex_config(_managed_block_re().sub(desired, current, count=1))
        return True
    stripped = current.rstrip()
    text = f"{stripped}\n\n{desired}" if stripped else desired
    write_codex_config(text)
    return True


def configure_provider(
    *,
    port: int,
    endpoint: str,
    env_key: str,
) -> None:
    """Write or update the launch-managed provider block in ``~/.codex/config.toml``."""
    _sync_managed_provider_block(port=port, endpoint=endpoint, env_key=env_key)


def restore_provider(*, env_key: str) -> None:
    """Legacy no-op: managed Codex config and keyring entries are never removed."""
    del env_key


def validate_agent_args(agent_args: list[str]) -> None:
    """Reject passthrough args that override managed provider settings."""
    index = 0
    while index < len(agent_args):
        arg = agent_args[index]
        payload = ""
        if arg in ("-c", "--config") and index + 1 < len(agent_args):
            payload = agent_args[index + 1]
            index += 2
        elif arg.startswith("-c") and arg != "-c":
            payload = arg[2:]
            index += 1
        else:
            index += 1
            continue

        normalized = payload.strip().strip("'\"")
        for pattern in _MANAGED_OVERRIDE_PATTERNS:
            if pattern.search(normalized):
                raise SystemExit(
                    "Cannot override cyt-managed Codex provider settings via -c. "
                    "Use --configure or edit ~/.codex/config.toml instead.",
                )


def provider_configured() -> bool:
    text = read_codex_config()
    return MANAGED_START in text and MANAGED_END in text


def managed_provider_base_url() -> str | None:
    """Return the base_url from the launch-managed Codex provider block, if present."""
    block = _extract_managed_block(read_codex_config())
    if block is None:
        return None
    url_match = re.search(
        r'base_url\s*=\s*"(?P<url>[^"]+)"',
        block,
    )
    if url_match is None:
        return None
    return url_match.group("url").rstrip("/")


def ensure_provider_configured(
    *,
    port: int,
    endpoint: str,
    env_key: str,
) -> None:
    """Add, update, or leave unchanged the launch-managed provider block."""
    _sync_managed_provider_block(port=port, endpoint=endpoint, env_key=env_key)


def run(
    *,
    config: dict[str, Any],
    port: int,
    endpoint: str,
    agent_args: list[str],
) -> int:
    """Exec Codex with optional config.toml provider wiring."""
    validate_agent_args(agent_args)
    env_key = codex_env_key_name(config)
    ensure_provider_configured(port=port, endpoint=endpoint, env_key=env_key)
    codex = find_codex()
    env = dict(os.environ)
    result = subprocess.run([codex, *agent_args], env=env, check=False)
    return int(result.returncode)
