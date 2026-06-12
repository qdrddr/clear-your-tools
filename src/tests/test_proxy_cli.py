"""Tests for ``cyt proxy`` CLI behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

import cyt.config as configs
from cyt.proxy.bootstrap import _apply_bm25_fallback_if_needed


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """
pruning:
  pipeline: [rerank]
defaults:
  remote:
    reranking_model_nick: rerank-qwen3-8b
models:
  rerankers:
    remote:
      - nick: rerank-qwen3-8b
        key_var_name: DEEPINFRA_API_KEY
""".strip(),
        encoding="utf-8",
    )


def test_upstream_cli_bm25_fallback_when_pruner_keys_missing(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    config = configs.load_config()
    assert configs.missing_proxy_env_var_names(config) == ["DEEPINFRA_API_KEY"]

    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=True)

    captured = capsys.readouterr()
    assert "fallback to BM25" in captured.err
    assert config["pruning"]["pipeline"] == ["bm25"]
    assert configs.missing_proxy_env_var_names(config) == []


def test_upstream_cli_keeps_pipeline_when_pruner_keys_set(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.setenv("DEEPINFRA_API_KEY", "test-key")

    config = configs.load_config()
    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=True)

    assert capsys.readouterr().err == ""
    assert config["pruning"]["pipeline"] == ["rerank"]


def test_no_upstream_cli_skips_bm25_when_remote_pruning_configured(
    isolated_config_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    user_config = isolated_config_paths["user_config"]
    _write_remote_rerank_user_config(user_config)
    monkeypatch.delenv("DEEPINFRA_API_KEY", raising=False)

    config = configs.load_config()
    _apply_bm25_fallback_if_needed(config, user_config, upstream_cli=False)

    assert capsys.readouterr().err == ""
    assert config["pruning"]["pipeline"] == ["rerank"]
    assert configs.missing_proxy_env_var_names(config) == ["DEEPINFRA_API_KEY"]
