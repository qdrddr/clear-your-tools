"""Tests for cyt-rproxy setup wizard helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cyt.config import default_model_nick, save_user_config
from cyt.proxy.setup import (
    build_setup_overlay,
    collect_key_var_names,
    domain_match_default_string,
    format_cost_prompt_default,
    format_env_lines,
    merge_model_entry,
    parse_cost_per_token,
    parse_domain_match,
    parse_env_file,
    per_token_to_usd_per_million,
    pipeline_from_choice,
    print_proxy_urls,
    usd_per_million_to_per_token,
    write_env_file,
)

_SAMPLE_MODEL = {
    "name": "claude-sonnet-4-6",
    "provider": "anthropic",
    "nick": "sonnet",
    "key_var_name": "ANTHROPIC_API_KEY",
    "max_tokens": 200000,
    "pricing": {
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 15e-06,
    },
}

_RERANK_MODEL = {
    "name": "Qwen/Qwen3-Reranker-8B",
    "provider": "deepinfra",
    "nick": "rerank-qwen3-8b",
    "key_var_name": "DEEPINFRA_API_KEY",
    "max_tokens": 32000,
    "pricing": {"input_cost_per_token": 5e-08},
}

_LLM_PRUNER = {
    "name": "inception/mercury-2",
    "provider": "openrouter",
    "nick": "mercury-2",
    "key_var_name": "OPENROUTER_API_KEY",
    "max_tokens": 1280000,
    "pricing": {
        "input_cost_per_token": 2.5e-07,
        "output_cost_per_token": 7.5e-07,
    },
}


class TestDomainMatchParsing:
    def test_parse_comma_separated(self) -> None:
        assert parse_domain_match("anthropic.com, api.anthropic.com") == [
            "anthropic.com",
            "api.anthropic.com",
        ]

    def test_empty_omits(self) -> None:
        assert parse_domain_match("") is None
        assert parse_domain_match("  ") is None

    def test_extracts_hostname_from_url(self) -> None:
        assert parse_domain_match("https://api.synthetic.new/openai/v1") == [
            "api.synthetic.new",
        ]

    def test_extracts_hostnames_from_mixed_urls_and_domains(self) -> None:
        assert parse_domain_match(
            "https://api.synthetic.new/openai/v1, anthropic.com",
        ) == ["api.synthetic.new", "anthropic.com"]

    def test_provider_default(self) -> None:
        assert domain_match_default_string("deepinfra") == "deepinfra.com"
        assert (
            domain_match_default_string(
                "anthropic",
                {"domain_match": ["custom.example"]},
            )
            == "custom.example"
        )


class TestCostPerTokenParsing:
    def test_usd_per_million_dollar_sign(self) -> None:
        assert parse_cost_per_token("$5") == pytest.approx(5e-06)

    def test_usd_per_million_plain_number(self) -> None:
        assert parse_cost_per_token("5") == pytest.approx(5e-06)
        assert parse_cost_per_token("3") == pytest.approx(3e-06)

    def test_scientific_per_token(self) -> None:
        assert parse_cost_per_token("5e-06") == pytest.approx(5e-06)
        assert parse_cost_per_token("1.5e-07") == pytest.approx(1.5e-07)

    def test_small_decimal_per_token(self) -> None:
        assert parse_cost_per_token("0.000003") == pytest.approx(3e-06)

    def test_usd_conversion_helpers(self) -> None:
        assert usd_per_million_to_per_token(5) == pytest.approx(5e-06)
        assert per_token_to_usd_per_million(5e-06) == pytest.approx(5)

    def test_format_prompt_default(self) -> None:
        assert format_cost_prompt_default(3e-06) == "$3"
        assert format_cost_prompt_default(5e-08) == "$0.05"


class TestDefaultModelNick:
    def test_slashes_and_spaces(self) -> None:
        assert (
            default_model_nick("openrouter", "openai/gpt-oss-120b")
            == "openrouter-openai-gpt-oss-120b"
        )

    def test_mixed_case_lowered(self) -> None:
        assert default_model_nick("DeepInfra", "Qwen/Reranker") == "deepinfra-qwen-reranker"


class TestPipelineFromChoice:
    def test_rerank_only(self) -> None:
        assert pipeline_from_choice("rerank") == ["rerank"]

    def test_both(self) -> None:
        assert pipeline_from_choice("both") == ["rerank", "llm"]


class TestMergeModelEntry:
    def test_replaces_same_nick(self) -> None:
        existing = [{"nick": "a", "name": "old"}, {"nick": "b", "name": "keep"}]
        updated = merge_model_entry(existing, {"nick": "a", "name": "new"})
        assert len(updated) == 2
        by_nick = {e["nick"]: e for e in updated}
        assert by_nick["a"]["name"] == "new"
        assert by_nick["b"]["name"] == "keep"

    def test_appends_new_nick(self) -> None:
        existing = [{"nick": "a"}]
        updated = merge_model_entry(existing, {"nick": "c"})
        assert len(updated) == 2


class TestCollectKeyVarNames:
    def test_dedupes(self) -> None:
        models = {
            "llm": {
                "remote": [
                    {"key_var_name": "OPENROUTER_API_KEY"},
                    {"key_var_name": "OPENROUTER_API_KEY"},
                ],
            },
            "rerankers": {"remote": [{"key_var_name": "DEEPINFRA_API_KEY"}]},
        }
        assert collect_key_var_names(models) == [
            "OPENROUTER_API_KEY",
            "DEEPINFRA_API_KEY",
        ]


class TestFormatEnvLines:
    def test_one_line_per_key(self) -> None:
        text = format_env_lines({"A": "1", "B": "2"})
        assert text == "A=1\nB=2\n"


class TestBuildSetupOverlay:
    def test_rerank_only_pipeline(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["rerank"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=None,
            upstream_llm_model=_SAMPLE_MODEL,
            llm_minimum_tools=50,
            reranker_minimum_tools=29,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[{"upstream": "anthropic", "url": "https://x/api", "kind": "anthropic"}],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        assert overlay["pruning"]["pipeline"] == ["rerank"]
        assert overlay["defaults"]["remote"]["reranking_model_nick"] == "rerank-qwen3-8b"
        assert "llm_model_nick" not in overlay["defaults"]["remote"]
        assert overlay["defaults"]["reranking_enabled"] is True
        llm_remote = overlay["models"]["llm"]["remote"]
        assert len(llm_remote) == 1
        assert llm_remote[0]["nick"] == "sonnet"

    def test_both_pipeline_includes_pruner(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["rerank", "llm"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=_LLM_PRUNER,
            upstream_llm_model=_SAMPLE_MODEL,
            llm_minimum_tools=50,
            reranker_minimum_tools=29,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[{"upstream": "anthropic", "url": "https://x/api", "kind": "anthropic"}],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        nicks = {e["nick"] for e in overlay["models"]["llm"]["remote"]}
        assert nicks == {"sonnet", "mercury-2"}
        assert overlay["defaults"]["remote"]["llm_model_nick"] == "mercury-2"


class TestSaveUserConfig:
    def test_merges_preserves_unrelated_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("custom:\n  keep: true\n", encoding="utf-8")
        save_user_config(path, {"defaults": {"mcp_tool_policy": "prune_all"}})
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["custom"]["keep"] is True
        assert loaded["defaults"]["mcp_tool_policy"] == "prune_all"


class TestEnvFile:
    def test_parse_and_write(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("EXISTING=old\n", encoding="utf-8")
        write_env_file(env_path, {"NEW": "secret"}, overwrite_existing=False)
        parsed = parse_env_file(env_path)
        assert parsed["EXISTING"] == "old"
        assert parsed["NEW"] == "secret"


class TestPrintProxyUrls:
    def test_prints_endpoints(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_proxy_urls(8834, ["anthropic", "openai"])
        out = capsys.readouterr().out
        assert "http://localhost:8834/anthropic" in out
        assert "http://localhost:8834/openai" in out
