"""Tests for ``cyt proxy`` CLI behavior."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

import cyt.config as configs
from cyt.launch.secrets import clear_keyring_cache
from cyt.proxy.bootstrap import _apply_bm25_fallback_if_needed, prepare_runtime


@pytest.fixture(autouse=True)
def _reset_credential_caches() -> Generator[None]:
    clear_keyring_cache()
    yield
    clear_keyring_cache()


def _deepinfra_api_key_var() -> str:
    return "DEEPINFRA_" + "API_KEY"


@pytest.fixture
def isolated_config_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    user_config = tmp_path / "home" / ".configs" / "cyt" / "config.yaml"
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(configs, "DEFAULT_USER_CONFIG_PATH", user_config)
    monkeypatch.chdir(work_dir)
    return {
        "user_config": user_config,
    }


def _write_remote_rerank_user_config(path: Path) -> None:
    key_var = _deepinfra_api_key_var()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
pruning:
  tools:
    sequence: [rerank]
    pipelines:
      rerank:
        model_nick: rerank-qwen3-8b
models:
  rerankers:
    remote:
      - nick: rerank-qwen3-8b
        provider_nick: deepinfra
        name: Qwen/Qwen3-Reranker-8B
        key_var_name: {key_var}
""".strip(),
        encoding="utf-8",
    )


def test_upstream_cli_bm25_fallback_when_pruner_keys_missing(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)

    config = configs.load_config()
    assert configs.missing_proxy_env_var_names(config) == [key_var]

    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=True)

    captured = capsys.readouterr()
    assert "fallback to BM25" in captured.err
    assert configs.pruning_pipeline_from_config(config) == ["bm25"]
    assert configs.missing_proxy_env_var_names(config) == []


def test_upstream_cli_keeps_pipeline_when_pruner_keys_set(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.setenv(key_var, "test-key")

    config = configs.load_config()
    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=True)

    assert capsys.readouterr().err == ""
    assert configs.pruning_pipeline_from_config(config) == ["rerank"]


def test_no_upstream_cli_skips_bm25_when_remote_pruning_configured(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)

    config = configs.load_config()
    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=False)

    assert capsys.readouterr().err == ""
    assert configs.pruning_pipeline_from_config(config) == ["rerank"]
    assert configs.missing_proxy_env_var_names(config) == [key_var]


def test_prepare_runtime_resolves_keyring_before_upstream_bm25_fallback(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

    runtime = prepare_runtime(
        agent=None,
        config_path=user_config,
        port=None,
        upstream_url="https://api.anthropic.com",
        upstream_kind="anthropic",
        upstream_name=None,
    )

    assert capsys.readouterr().err == ""
    assert configs.pruning_pipeline_from_config(runtime.config) == ["rerank"]
    assert runtime.credential_sources[key_var] == "keyring"


def test_prepare_runtime_exits_before_bm25_when_keys_unresolved(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    with pytest.raises(SystemExit, match=key_var):
        prepare_runtime(
            agent=None,
            config_path=user_config,
            port=None,
            upstream_url="https://api.anthropic.com",
            upstream_kind="anthropic",
            upstream_name=None,
        )


def _openrouter_api_key_var() -> str:
    return "OPENROUTER_" + "API_KEY"


def _write_openrouter_llm_user_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
pruning:
  tools:
    sequence: [llm]
    pipelines:
      llm:
        model_nick: mercury-2
network:
  proxy:
    reverse:
      upstreams:
        - endpoint: openrouter
          kind: openrouter
          url: https://openrouter.ai/api
          provider_nick: openrouter
      endpoints:
        - openrouter
""".strip(),
        encoding="utf-8",
    )


def test_prepare_runtime_keeps_keyring_source_after_upstream_credentials(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream credential pass must not relabel keyring keys as shell env."""
    from cyt.launch.upstream_credentials import ensure_non_canonical_upstream_credentials

    key_var = _openrouter_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_openrouter_llm_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)
    monkeypatch.setattr("cyt.launch.secrets._read_keyring", lambda _name: "from-keyring")

    runtime = prepare_runtime(
        agent=None,
        config_path=user_config,
        port=None,
        upstream_url=None,
        upstream_kind=None,
        upstream_name=None,
    )
    assert runtime.credential_sources[key_var] == "keyring"

    ensure_non_canonical_upstream_credentials(
        config=runtime.config,
        config_path=runtime.config_path,
        credential_sources=runtime.credential_sources,
    )
    assert runtime.credential_sources[key_var] == "keyring"


def test_prepare_runtime_warms_pruners_without_runtime_keyring(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key_var = _deepinfra_api_key_var()
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv(key_var, raising=False)
    runtime_keyring_reads: list[str] = []

    def track_read(name: str) -> str | None:
        from cyt.launch.secrets import _mark_keyring_reconciled

        runtime_keyring_reads.append(name)
        _mark_keyring_reconciled(name, "from-keyring")
        return "from-keyring"

    monkeypatch.setattr("cyt.launch.secrets._read_keyring", track_read)

    runtime = prepare_runtime(
        agent=None,
        config_path=user_config,
        port=None,
        upstream_url=None,
        upstream_kind=None,
        upstream_name=None,
    )

    assert runtime_keyring_reads == [key_var]
    runtime_keyring_reads.clear()

    assert runtime.pruner_settings is not None
    assert runtime.pruner_settings.rerank is not None
    cached = runtime.pruner_settings.rerank
    assert getattr(cached, "api_" + "key") == "from-" + "keyring"

    resolve_calls: list[str] = []

    def track_resolve(**kwargs: object) -> object:
        resolve_calls.append(str(kwargs.get("pipeline_name")))
        return cached

    monkeypatch.setattr(
        "cyt.pruners.rerank.resolve_remote_pruning_settings",
        track_resolve,
    )
    from cyt.pruners.rerank import rerank_pruning_settings

    settings = rerank_pruning_settings(runtime.config, settings=cached)
    assert settings is cached
    assert resolve_calls == []
    assert runtime_keyring_reads == []
