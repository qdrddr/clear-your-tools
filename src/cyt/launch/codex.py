"""Codex launcher through the CYT reverse proxy."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from cyt.launch.config import codex_env_key_name
from cyt.launch.secrets import delete_keyring_secret

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


def _strip_managed_block(text: str) -> str:
    pattern = re.compile(
        rf"{re.escape(MANAGED_START)}.*?{re.escape(MANAGED_END)}\n?",
        re.DOTALL,
    )
    return pattern.sub("", text)


def read_codex_config() -> str:
    if CODEX_CONFIG_PATH.is_file():
        return CODEX_CONFIG_PATH.read_text(encoding="utf-8")
    return ""


def write_codex_config(text: str) -> None:
    CODEX_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG_PATH.write_text(text, encoding="utf-8")


def configure_provider(
    *,
    port: int,
    endpoint: str,
    env_key: str,
) -> None:
    """Write launch-managed provider block into ~/.codex/config.toml."""
    base_url = f"http://127.0.0.1:{port}/{endpoint}/v1"
    existing = _strip_managed_block(read_codex_config()).rstrip()
    block = _provider_block(base_url=base_url, env_key=env_key)
    if existing:
        text = f"{existing}\n\n{block}"
    else:
        text = block
    write_codex_config(text)


def restore_provider(*, env_key: str) -> None:
    """Remove launch-managed provider block and keyring secret."""
    write_codex_config(_strip_managed_block(read_codex_config()).rstrip() + "\n")
    delete_keyring_secret(env_key)


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
    text = read_codex_config()
    pattern = re.compile(
        rf"{re.escape(MANAGED_START)}.*?{re.escape(MANAGED_END)}",
        re.DOTALL,
    )
    match = pattern.search(text)
    if match is None:
        return None
    url_match = re.search(
        r'base_url\s*=\s*"(?P<url>[^"]+)"',
        match.group(0),
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
    """Write or refresh the launch-managed provider block when settings changed."""
    expected_base_url = f"http://127.0.0.1:{port}/{endpoint}/v1"
    if managed_provider_base_url() == expected_base_url and provider_configured():
        return
    configure_provider(port=port, endpoint=endpoint, env_key=env_key)


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
