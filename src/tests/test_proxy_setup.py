"""Tests for cyt-rproxy setup wizard helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from cyt.config import (
    bundled_user_config_sections,
    default_model_nick,
    save_user_config,
)
from cyt.proxy.setup import (
    PRIMARY_TOO_CHEAP_MESSAGE,
    _prompt_custom_model,
    _prompt_key_var_name,
    build_setup_overlay,
    catalog_has_upstream_domain_match,
    collect_key_var_names,
    domain_match_default_string,
    filter_catalog_by_max_input_cost,
    filter_catalog_by_upstreams,
    format_cost_prompt_default,
    format_env_lines,
    input_usd_per_million,
    max_pruner_input_cost_per_token,
    merge_model_entry,
    model_input_cost_per_token,
    normalize_base_url,
    normalize_upstream_url,
    parse_cost_per_token,
    parse_domain_match,
    parse_env_file,
    per_token_to_usd_per_million,
    pipeline_from_choice,
    print_primary_too_cheap_warning,
    print_proxy_urls,
    pruner_input_cost_error,
    recommended_pipeline_default_index,
    upstream_hostnames_default,
    upstream_url_default,
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

    def test_upstream_urls_default(self) -> None:
        upstreams = [
            {"url": "https://api.anthropic.com"},
            {"url": "https://openrouter.ai/api"},
        ]
        assert upstream_hostnames_default(upstreams) == ("api.anthropic.com,openrouter.ai")
        assert (
            domain_match_default_string(
                "anthropic",
                upstreams=upstreams,
            )
            == "api.anthropic.com,openrouter.ai"
        )

    def test_upstream_urls_override_catalog_domain_match(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        assert (
            domain_match_default_string(
                "anthropic",
                {"domain_match": ["anthropic.com"]},
                upstreams=upstreams,
            )
            == "api.anthropic.com"
        )


class TestPrimaryModelPricing:
    def test_model_input_cost_per_token(self) -> None:
        assert model_input_cost_per_token(_SAMPLE_MODEL) == pytest.approx(3e-06)
        assert model_input_cost_per_token({}) is None

    def test_input_usd_per_million(self) -> None:
        assert input_usd_per_million(_SAMPLE_MODEL) == pytest.approx(3)

    def test_too_cheap_warning(self, capsys: pytest.CaptureFixture[str]) -> None:
        cheap = {
            "pricing": {"input_cost_per_token": 0.2e-06},
        }
        print_primary_too_cheap_warning(cheap)
        assert PRIMARY_TOO_CHEAP_MESSAGE in capsys.readouterr().out

        print_primary_too_cheap_warning(_SAMPLE_MODEL)
        assert capsys.readouterr().out == ""

    def test_recommended_pipeline_default_index(self) -> None:
        cheap = {"pricing": {"input_cost_per_token": 2e-06}}
        expensive = {"pricing": {"input_cost_per_token": 3e-06}}
        assert recommended_pipeline_default_index(cheap) == 0
        assert recommended_pipeline_default_index(expensive) == 1
        assert (
            recommended_pipeline_default_index(
                {"pricing": {"input_cost_per_token": 2.5e-06}},
            )
            == 0
        )

    def test_max_pruner_input_cost_per_token(self) -> None:
        assert max_pruner_input_cost_per_token(_SAMPLE_MODEL) == pytest.approx(3e-07)

    def test_filter_catalog_by_max_input_cost(self) -> None:
        catalog: list[dict[str, Any]] = [
            {"nick": "cheap", "pricing": {"input_cost_per_token": 2.5e-07}},
            {"nick": "mid", "pricing": {"input_cost_per_token": 1e-06}},
            {"nick": "no-price"},
        ]
        filtered = filter_catalog_by_max_input_cost(catalog, 3e-07)
        assert [e["nick"] for e in filtered] == ["cheap"]

    def test_pruner_input_cost_error(self) -> None:
        max_cost = max_pruner_input_cost_per_token(_SAMPLE_MODEL)
        assert max_cost is not None
        assert max_cost == pytest.approx(3e-07)
        assert (
            pruner_input_cost_error(
                {"pricing": {"input_cost_per_token": 2.5e-07}},
                max_cost,
            )
            is None
        )
        error = pruner_input_cost_error(
            {"pricing": {"input_cost_per_token": 1e-06}},
            max_cost,
        )
        assert error is not None
        assert "10x cheaper" in error
        assert "$0.3" in error


class TestFilterCatalogByUpstreams:
    _CATALOG: list[dict[str, Any]] = [
        {"nick": "openrouter-model", "domain_match": ["openrouter.ai"]},
        {"nick": "anthropic-model", "domain_match": ["api.anthropic.com"]},
        {"nick": "no-domain-match"},
        {"nick": "openai-model", "domain_match": ["openai.com"]},
    ]

    def test_filters_by_upstream_hostname(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        filtered = filter_catalog_by_upstreams(self._CATALOG, upstreams)
        assert [e["nick"] for e in filtered] == ["anthropic-model"]

    def test_returns_all_when_no_match(self) -> None:
        upstreams = [{"url": "https://api.example.com/v1"}]
        filtered = filter_catalog_by_upstreams(self._CATALOG, upstreams)
        assert filtered == self._CATALOG

    def test_returns_all_when_upstreams_empty(self) -> None:
        assert filter_catalog_by_upstreams(self._CATALOG, []) == self._CATALOG
        assert filter_catalog_by_upstreams(self._CATALOG, None) == self._CATALOG

    def test_has_match_when_filtered(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        assert catalog_has_upstream_domain_match(self._CATALOG, upstreams) is True

    def test_no_match_for_unknown_host(self) -> None:
        upstreams = [{"url": "https://api.example.com/v1"}]
        assert catalog_has_upstream_domain_match(self._CATALOG, upstreams) is False


class TestUpstreamUrlDefault:
    def test_keeps_api_path(self) -> None:
        upstreams = [{"url": "https://api.openai.com/v1"}]
        assert upstream_url_default(upstreams) == "https://api.openai.com/v1"

    def test_falls_back_to_legacy_host_url(self) -> None:
        upstreams = [{"host_url": "https://api.anthropic.com"}]
        assert upstream_url_default(upstreams) == "https://api.anthropic.com"

    def test_empty_upstreams(self) -> None:
        assert upstream_url_default([]) is None


class TestNormalizeUpstreamUrl:
    def test_preserves_path(self) -> None:
        assert normalize_upstream_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"

    def test_openrouter_api_path(self) -> None:
        assert normalize_upstream_url("https://openrouter.ai/api/") == "https://openrouter.ai/api"


class TestNormalizeBaseUrl:
    def test_preserves_path(self) -> None:
        assert normalize_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"

    def test_openrouter_api_path(self) -> None:
        assert normalize_base_url("https://openrouter.ai/api/") == "https://openrouter.ai/api"


class TestCostPerTokenParsing:
    def test_usd_per_million_dollar_sign(self) -> None:
        assert parse_cost_per_token("$5") == pytest.approx(5e-06)

    def test_usd_per_million_fractional_dollar_sign(self) -> None:
        assert parse_cost_per_token("$0.05") == 5e-08
        assert parse_cost_per_token("$0.05") != 5.0000000000000004e-08

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
        assert format_cost_prompt_default(0) == "$0"


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


class TestPromptCustomModel:
    def test_omits_base_url_when_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                "deepinfra",
                "custom/reranker",
                "",
                "DEEPINFRA_API_KEY",
                "32000",
                "0.05",
                "0",
                "",
                "",
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = _prompt_custom_model(
            kind_label="reranker model",
            prompt_base_url=True,
        )
        assert "base_url" not in result

    def test_prompts_base_url_when_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                "deepinfra",
                "custom/reranker",
                "",
                "DEEPINFRA_API_KEY",
                "32000",
                "0.05",
                "0",
                "https://api.deepinfra.com/v1",
                "",
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = _prompt_custom_model(
            kind_label="reranker model",
            default_base_url="https://api.anthropic.com",
            prompt_base_url=True,
        )
        assert result["base_url"] == "https://api.deepinfra.com/v1"

    def test_reprompts_when_input_cost_too_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                "deepinfra",
                "custom/reranker",
                "",
                "DEEPINFRA_API_KEY",
                "32000",
                "3",
                "0.05",
                "0",
                "",
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = _prompt_custom_model(
            kind_label="reranker model",
            max_input_cost_per_token=3e-07,
        )
        assert result["pricing"]["input_cost_per_token"] == pytest.approx(5e-08)


class TestPromptKeyVarName:
    def test_accepts_catalog_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert _prompt_key_var_name(default="OPENROUTER_API_KEY") == "OPENROUTER_API_KEY"

    def test_reprompts_until_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(["", "  ", "ANTHROPIC_API_KEY"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        assert _prompt_key_var_name() == "ANTHROPIC_API_KEY"


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
            upstream_llm_models=[_SAMPLE_MODEL],
            llm_minimum_tools=50,
            reranker_minimum_tools=29,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "upstream": "anthropic",
                    "url": "https://openrouter.ai/api",
                    "kind": "anthropic",
                },
            ],
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
        saved_upstream = overlay["network"]["proxy"]["reverse"]["upstreams"][0]
        assert saved_upstream == {
            "upstream": "anthropic",
            "url": "https://openrouter.ai/api",
            "kind": "anthropic",
        }

    def test_both_pipeline_includes_pruner(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["rerank", "llm"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=_LLM_PRUNER,
            upstream_llm_models=[_SAMPLE_MODEL],
            llm_minimum_tools=50,
            reranker_minimum_tools=29,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "upstream": "anthropic",
                    "url": "https://openrouter.ai/api",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        nicks = {e["nick"] for e in overlay["models"]["llm"]["remote"]}
        assert nicks == {"sonnet", "mercury-2"}
        assert overlay["defaults"]["remote"]["llm_model_nick"] == "mercury-2"

    def test_multiple_primary_upstream_models(self) -> None:
        second_primary = {**_SAMPLE_MODEL, "nick": "opus", "name": "claude-opus-4-7"}
        overlay = build_setup_overlay(
            pipeline=["rerank"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=None,
            upstream_llm_models=[_SAMPLE_MODEL, second_primary],
            llm_minimum_tools=50,
            reranker_minimum_tools=29,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "upstream": "anthropic",
                    "url": "https://api.anthropic.com",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        nicks = [e["nick"] for e in overlay["models"]["llm"]["remote"]]
        assert nicks == ["sonnet", "opus"]


class TestSaveUserConfig:
    def test_merges_preserves_unrelated_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("custom:\n  keep: true\n", encoding="utf-8")
        save_user_config(path, {"defaults": {"mcp_tool_policy": "prune_all"}})
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert loaded["custom"]["keep"] is True
        assert loaded["defaults"]["mcp_tool_policy"] == "prune_all"

    def test_apply_bundled_sections_replaces_stale_ssl_and_per_tool(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "config.yaml"
        path.write_text(
            "\n".join(
                [
                    "pruning:",
                    "  per_tool:",
                    "    Agent: prune_optional",
                    "  pipeline:",
                    "  - rerank",
                    "network:",
                    "  proxy:",
                    "    reverse:",
                    "      port: 8834",
                    "      http2:",
                    "        ssl:",
                    "          keyfile: src/crt/key.pem",
                    "          certfile: src/crt/cert.pem",
                ],
            ),
            encoding="utf-8",
        )
        save_user_config(
            path,
            {"pruning": {"pipeline": ["rerank"]}},
            apply_bundled_sections=True,
        )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        bundled = bundled_user_config_sections()
        assert loaded["pruning"]["per_tool"] == bundled["pruning"]["per_tool"]
        assert "Agent" not in loaded["pruning"]["per_tool"]
        ssl = loaded["network"]["proxy"]["reverse"]["http2"]["ssl"]
        assert ssl == bundled["network"]["proxy"]["reverse"]["http2"]["ssl"]
        assert ssl["keyfile"] == "~/.config/cyt/crt/key.pem"
        assert ssl["certfile"] == "~/.config/cyt/crt/cert.pem"
        assert loaded["network"]["proxy"]["reverse"]["port"] == 8834


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
