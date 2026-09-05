"""Tests for cyt setup wizard helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from cyt.config import (
    bundled_user_config_sections,
    default_model_nick,
    merge_model_entry,
    save_user_config,
)
from cyt.proxy.setup_wizard import (
    _catalog_entries,
    _catalog_merge_config,
    _prompt_custom_model,
    _prompt_key_var_name,
    _prompt_skills,
    _prompt_upstreams,
    apply_upstream_cli_to_config,
    build_setup_overlay,
    build_upstream_cli_overlay,
    catalog_has_upstream_domain_match,
    collect_key_var_names,
    derive_second_level_domain_from_hostname,
    derive_upstream_name_from_url,
    domain_match_default_string,
    filter_catalog_by_max_input_cost,
    filter_catalog_by_upstreams,
    format_cost_prompt_default,
    format_env_lines,
    has_models_missing_costs,
    input_usd_per_million,
    iter_incomplete_remote_models,
    iter_models_missing_costs,
    key_var_name_from_provider,
    max_pruner_input_cost_per_token,
    merge_endpoints,
    merge_setup_overlay,
    merge_upstream_entry,
    model_input_cost_per_token,
    model_missing_cost_fields,
    model_missing_metadata_fields,
    model_output_cost_per_token,
    normalize_base_url,
    normalize_upstream_kind,
    normalize_upstream_url,
    parse_cost_per_token,
    parse_domain_match,
    parse_env_file,
    parse_path_list,
    per_token_to_usd_per_million,
    pipeline_from_choice,
    print_primary_too_cheap_warning,
    print_proxy_urls,
    prompt_incomplete_models_in_config,
    pruner_input_cost_error,
    recommended_pipeline_default_index,
    run_add_costs_wizard,
    run_setup,
    skills_pipeline_default_from_tool_pipeline,
    upsert_remote_model,
    upstream_entry_endpoint,
    upstream_hostnames_default,
    upstream_url_default,
    upstreams_for_config,
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

    def test_parse_path_list(self) -> None:
        assert parse_path_list("~/.claude/skills, .codex/skills") == [
            "~/.claude/skills",
            ".codex/skills",
        ]
        assert parse_path_list("") is None

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

    def test_base_url_infers_domain_match(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        assert (
            domain_match_default_string(
                "openrouter",
                upstreams=upstreams,
                base_url="https://openrouter.ai/api",
            )
            == "openrouter.ai"
        )

    def test_entry_base_url_infers_domain_match(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        assert (
            domain_match_default_string(
                "openrouter",
                {"base_url": "https://openrouter.ai/api"},
                upstreams=upstreams,
            )
            == "openrouter.ai"
        )


class TestPrimaryModelPricing:
    def test_model_input_cost_per_token(self) -> None:
        assert model_input_cost_per_token(_SAMPLE_MODEL) == pytest.approx(3e-06)
        assert model_input_cost_per_token({}) is None

    def test_model_output_cost_per_token(self) -> None:
        assert model_output_cost_per_token(_SAMPLE_MODEL) == pytest.approx(15e-06)
        assert model_output_cost_per_token(_RERANK_MODEL) is None
        assert model_output_cost_per_token({}) is None

    def test_model_missing_metadata_fields(self) -> None:
        assert model_missing_metadata_fields(_SAMPLE_MODEL) == ["domain_match"]
        assert model_missing_metadata_fields(_RERANK_MODEL) == ["domain_match"]
        assert model_missing_metadata_fields({"name": "x"}) == [
            "provider",
            "domain_match",
        ]
        assert model_missing_metadata_fields({"provider": "  "}) == [
            "provider",
            "domain_match",
        ]

    def test_model_missing_metadata_resolves_provider_nick(self) -> None:
        config: dict[str, Any] = {
            "models": {
                "providers": [
                    {
                        "provider_nick": "anthropic",
                        "provider": "anthropic",
                        "domain_match": ["api.anthropic.com"],
                    },
                ],
                "llm": {
                    "remote": [
                        {
                            "nick": "sonnet46",
                            "name": "claude-sonnet-4-6",
                            "provider_nick": "anthropic",
                        },
                    ],
                },
            },
        }
        entry = config["models"]["llm"]["remote"][0]
        assert model_missing_metadata_fields(entry, config=config) == []
        assert iter_incomplete_remote_models(config) == []

    def test_model_missing_cost_fields(self) -> None:
        assert model_missing_cost_fields(_SAMPLE_MODEL) == []
        assert model_missing_cost_fields(_RERANK_MODEL) == ["output_cost_per_token"]
        assert model_missing_cost_fields({"name": "x"}) == [
            "input_cost_per_token",
            "output_cost_per_token",
        ]

    def test_iter_incomplete_remote_models(self) -> None:
        config = {
            "models": {
                "llm": {
                    "remote": [
                        {
                            "name": "claude-sonnet-4-6",
                            "provider_nick": "anthropic",
                            "nick": "sonnet",
                        },
                        {"nick": "synced", "name": "provider/model"},
                    ],
                },
                "rerankers": {
                    "remote": [
                        {
                            "name": "Qwen/Qwen3-Reranker-8B",
                            "provider_nick": "deepinfra",
                            "nick": "rerank-qwen3-8b",
                        },
                    ],
                },
            },
        }
        incomplete = iter_incomplete_remote_models(config)
        assert [entry.get("nick") for _kind, entry in incomplete] == ["synced"]

    def test_iter_models_missing_costs(self) -> None:
        config = {
            "models": {
                "llm": {
                    "remote": [
                        _SAMPLE_MODEL,
                        {"nick": "synced", "name": "provider/model"},
                    ],
                },
                "rerankers": {
                    "remote": [_RERANK_MODEL],
                },
            },
        }
        missing = iter_models_missing_costs(config)
        assert [entry.get("nick") for _kind, entry in missing] == [
            "synced",
            "rerank-qwen3-8b",
        ]
        assert has_models_missing_costs(config) is True
        assert has_models_missing_costs({"models": {"llm": {"remote": [_SAMPLE_MODEL]}}}) is False

    def test_prompt_incomplete_models_in_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        config: dict[str, Any] = {
            "models": {
                "llm": {
                    "remote": [
                        {"nick": "synced", "name": "provider/model"},
                    ],
                },
            },
        }
        responses = iter(["openrouter", "api.openrouter.ai"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        changed = prompt_incomplete_models_in_config(config)
        assert changed is True
        entry = config["models"]["llm"]["remote"][0]
        assert entry["provider_nick"] == "openrouter"
        assert "provider" not in entry
        assert "domain_match" not in entry
        providers = config["models"]["providers"]
        assert isinstance(providers, list)
        provider = next(
            item
            for item in providers
            if isinstance(item, dict) and item.get("provider_nick") == "openrouter"
        )
        assert provider["provider"] == "openrouter"
        assert provider["domain_match"] == ["api.openrouter.ai"]
        assert "pricing" not in entry

    def test_run_add_costs_wizard(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "models": {
                        "llm": {
                            "remote": [
                                {
                                    "nick": "synced",
                                    "name": "provider/model",
                                    "provider": "openrouter",
                                    "domain_match": ["api.openrouter.ai"],
                                },
                            ],
                        },
                        "rerankers": {
                            "remote": [
                                {
                                    **_RERANK_MODEL,
                                    "domain_match": ["deepinfra.com"],
                                },
                            ],
                        },
                    },
                },
            ),
            encoding="utf-8",
        )
        responses = iter(["3", "15", "0.05"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        run_add_costs_wizard(config_path)
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        llm_pricing = saved["models"]["llm"]["remote"][0]["pricing"]
        rerank_pricing = saved["models"]["rerankers"]["remote"][0]["pricing"]
        assert llm_pricing["input_cost_per_token"] == pytest.approx(3e-06)
        assert llm_pricing["output_cost_per_token"] == pytest.approx(15e-06)
        assert rerank_pricing["output_cost_per_token"] == pytest.approx(5e-08)

    def test_input_usd_per_million(self) -> None:
        assert input_usd_per_million(_SAMPLE_MODEL) == pytest.approx(3)

    def test_key_var_name_from_provider(self) -> None:
        assert key_var_name_from_provider("deepinfra") == "DEEPINFRA_API_KEY"
        assert key_var_name_from_provider("openrouter") == "OPENROUTER_API_KEY"

    def test_recommended_pipeline_default_index(self) -> None:
        too_cheap = {"pricing": {"input_cost_per_token": 0.2e-06}}
        moderate = {"pricing": {"input_cost_per_token": 2e-06}}
        expensive = {"pricing": {"input_cost_per_token": 3e-06}}
        assert recommended_pipeline_default_index(too_cheap) == 3
        assert recommended_pipeline_default_index({}) == 0
        assert recommended_pipeline_default_index(moderate) == 0
        assert recommended_pipeline_default_index(expensive) == 1
        assert (
            recommended_pipeline_default_index(
                {"pricing": {"input_cost_per_token": 2.5e-06}},
            )
            == 0
        )

    def test_print_primary_too_cheap_warning(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        print_primary_too_cheap_warning(
            {"pricing": {"input_cost_per_token": 0.2e-06}},
        )
        assert "BM25-only pruning is recommended" in capsys.readouterr().out
        capsys.readouterr()
        print_primary_too_cheap_warning(
            {"pricing": {"input_cost_per_token": 2e-06}},
        )
        assert capsys.readouterr().out == ""

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


_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES: list[dict[str, Any]] = [
    {"nick": "openrouter-model", "domain_match": ["openrouter.ai"]},
    {"nick": "anthropic-model", "domain_match": ["api.anthropic.com"]},
    {"nick": "no-domain-match"},
    {"nick": "openai-model", "domain_match": ["openai.com"]},
]


class TestFilterCatalogByUpstreams:
    def test_filters_by_upstream_hostname(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        filtered = filter_catalog_by_upstreams(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, upstreams)
        assert [e["nick"] for e in filtered] == ["anthropic-model"]

    def test_returns_all_when_no_match(self) -> None:
        upstreams = [{"url": "https://api.example.com/v1"}]
        filtered = filter_catalog_by_upstreams(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, upstreams)
        assert filtered == _FILTER_CATALOG_BY_UPSTREAMS_ENTRIES

    def test_returns_all_when_upstreams_empty(self) -> None:
        assert (
            filter_catalog_by_upstreams(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, [])
            == _FILTER_CATALOG_BY_UPSTREAMS_ENTRIES
        )
        assert (
            filter_catalog_by_upstreams(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, None)
            == _FILTER_CATALOG_BY_UPSTREAMS_ENTRIES
        )

    def test_has_match_when_filtered(self) -> None:
        upstreams = [{"url": "https://api.anthropic.com"}]
        assert (
            catalog_has_upstream_domain_match(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, upstreams)
            is True
        )

    def test_no_match_for_unknown_host(self) -> None:
        upstreams = [{"url": "https://api.example.com/v1"}]
        assert (
            catalog_has_upstream_domain_match(_FILTER_CATALOG_BY_UPSTREAMS_ENTRIES, upstreams)
            is False
        )


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


class TestDeriveUpstreamNameFromUrl:
    def test_api_subdomain(self) -> None:
        assert derive_upstream_name_from_url("https://api.anthropic.com") == "anthropic"
        assert derive_upstream_name_from_url("https://api.openai.com/v1") == "openai"

    def test_two_part_hostname(self) -> None:
        assert derive_upstream_name_from_url("https://openrouter.ai/api") == "openrouter"


class TestDeriveSecondLevelDomainFromHostname:
    def test_api_subdomain_uses_sld(self) -> None:
        assert derive_second_level_domain_from_hostname("api.openrouter.ai") == "openrouter"
        assert derive_second_level_domain_from_hostname("api.anthropic.com") == "anthropic"

    def test_two_part_hostname(self) -> None:
        assert derive_second_level_domain_from_hostname("openrouter.ai") == "openrouter"


class TestBuildUpstreamCliOverlay:
    def test_minimal_anthropic_upstream(self) -> None:
        overlay = build_upstream_cli_overlay(
            "https://api.anthropic.com",
            "anthropic",
        )
        assert overlay["network"]["proxy"]["reverse"] == {
            "upstreams": [
                {
                    "endpoint": "anthropic",
                    "kind": "anthropic",
                    "url": "https://api.anthropic.com",
                },
            ],
            "endpoints": ["anthropic"],
        }

    def test_openai_kind_on_custom_host(self) -> None:
        overlay = build_upstream_cli_overlay(
            "https://openrouter.ai/api",
            "openai",
        )
        reverse = overlay["network"]["proxy"]["reverse"]
        assert reverse["upstreams"][0]["endpoint"] == "openrouter"
        assert reverse["upstreams"][0]["kind"] == "openai"
        assert reverse["endpoints"] == ["openrouter"]

    def test_explicit_upstream_name(self) -> None:
        overlay = build_upstream_cli_overlay(
            "https://openrouter.ai/api",
            "anthropic",
            upstream_name="anthropic",
        )
        reverse = overlay["network"]["proxy"]["reverse"]
        assert reverse["upstreams"][0]["endpoint"] == "anthropic"
        assert reverse["endpoints"] == ["anthropic"]

    def test_rejects_empty_upstream_name(self) -> None:
        with pytest.raises(ValueError, match="upstream name must not be empty"):
            build_upstream_cli_overlay(
                "https://api.anthropic.com",
                "anthropic",
                upstream_name="  ",
            )

    def test_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValueError, match="Invalid upstream kind"):
            build_upstream_cli_overlay("https://api.anthropic.com", "gemini")

    @pytest.mark.parametrize(
        ("alias", "canonical"),
        [
            ("claude", "anthropic"),
            ("claude-code", "anthropic"),
            ("codex", "openai"),
            ("openrouter", "anthropic"),
        ],
    )
    def test_upstream_kind_aliases(self, alias: str, canonical: str) -> None:
        overlay = build_upstream_cli_overlay("https://api.example.com", alias)
        assert overlay["network"]["proxy"]["reverse"]["upstreams"][0]["kind"] == canonical

    def test_normalize_upstream_kind(self) -> None:
        assert normalize_upstream_kind("Claude-Code") == "anthropic"
        assert normalize_upstream_kind("CODEX") == "openai"
        assert normalize_upstream_kind("openrouter") == "anthropic"

    def test_upstreams_for_config_normalizes_legacy_openrouter_kind(self) -> None:
        serialized = upstreams_for_config(
            [
                {
                    "endpoint": "openrouter",
                    "kind": "openrouter",
                    "url": "https://openrouter.ai/api",
                },
            ],
        )
        assert serialized[0]["kind"] == "anthropic"

    def test_print_configured_upstreams_accepts_legacy_openrouter_kind(self) -> None:
        import contextlib
        from io import StringIO

        from cyt.proxy.setup_wizard import _print_configured_upstreams

        upstreams = [
            {
                "endpoint": "openrouter",
                "kind": "openrouter",
                "url": "https://openrouter.ai/api",
            },
        ]
        buffer = StringIO()
        with contextlib.redirect_stdout(buffer):
            _print_configured_upstreams(upstreams)
        out = buffer.getvalue()
        assert "openrouter" in out
        assert "anthropic" in out

    def test_upstreams_for_config_normalizes_kind_aliases(self) -> None:
        serialized = upstreams_for_config(
            [{"endpoint": "anthropic", "kind": "claude-code", "url": "https://api.anthropic.com"}],
        )
        assert serialized[0]["kind"] == "anthropic"


class TestApplyUpstreamCliToConfig:
    def test_writes_to_config_yaml(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text("stats:\n  enabled: true\n", encoding="utf-8")
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.anthropic.com",
            upstream_kind="anthropic",
        )
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        assert saved["stats"]["enabled"] is True
        reverse = saved["network"]["proxy"]["reverse"]
        assert reverse["upstreams"] == [
            {
                "endpoint": "anthropic",
                "kind": "anthropic",
                "url": "https://api.anthropic.com",
            },
        ]
        assert reverse["endpoints"] == ["anthropic"]

    def test_explicit_upstream_name(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://openrouter.ai/api",
            upstream_kind="anthropic",
            upstream_name="anthropic",
        )
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        reverse = saved["network"]["proxy"]["reverse"]
        assert reverse["upstreams"][0]["endpoint"] == "anthropic"
        assert reverse["endpoints"] == ["anthropic"]

    def test_preserves_existing_upstreams(self, tmp_path: Path) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "\n".join(
                [
                    "network:",
                    "  proxy:",
                    "    reverse:",
                    "      upstreams:",
                    "        - endpoint: openai",
                    "          kind: openai",
                    "          url: https://api.openai.com",
                    "      endpoints:",
                    "      - openai",
                ],
            ),
            encoding="utf-8",
        )
        apply_upstream_cli_to_config(
            config_path,
            upstream_url="https://api.anthropic.com",
            upstream_kind="anthropic",
        )
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        reverse = saved["network"]["proxy"]["reverse"]
        assert [entry["endpoint"] for entry in reverse["upstreams"]] == [
            "openai",
            "anthropic",
        ]
        assert reverse["endpoints"] == ["openai", "anthropic"]


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

    def test_llm_only(self) -> None:
        assert pipeline_from_choice("llm") == ["llm"]

    def test_bm25_only(self) -> None:
        assert pipeline_from_choice("bm25") == ["bm25"]

    def test_both(self) -> None:
        assert pipeline_from_choice("both") == ["rerank", "llm"]


class TestSkillsPipelineDefaultFromToolPipeline:
    def test_single_stage_mappings(self) -> None:
        assert skills_pipeline_default_from_tool_pipeline(["rerank"]) == "rerank"
        assert skills_pipeline_default_from_tool_pipeline(["llm"]) == "llm"
        assert skills_pipeline_default_from_tool_pipeline(["bm25"]) == "bm25"

    def test_both_defaults_to_rerank(self) -> None:
        assert skills_pipeline_default_from_tool_pipeline(["rerank", "llm"]) == "rerank"

    def test_empty_defaults_to_bm25(self) -> None:
        assert skills_pipeline_default_from_tool_pipeline([]) == "bm25"


class TestUpsertRemoteModel:
    def test_replaces_same_nick(self) -> None:
        existing = [{"nick": "a", "name": "old"}, {"nick": "b", "name": "keep"}]
        updated = upsert_remote_model(existing, {"nick": "a", "name": "new"})
        assert len(updated) == 2
        by_nick = {e["nick"]: e for e in updated}
        assert by_nick["a"]["name"] == "new"
        assert by_nick["b"]["name"] == "keep"

    def test_appends_new_nick(self) -> None:
        existing = [{"nick": "a"}]
        updated = upsert_remote_model(existing, {"nick": "c"})
        assert len(updated) == 2


class TestMergeUpstreamEntry:
    def test_replaces_same_name(self) -> None:
        existing = [
            {"endpoint": "anthropic", "url": "https://old.example"},
            {"endpoint": "openai", "url": "https://api.openai.com"},
        ]
        updated = merge_upstream_entry(
            existing,
            {"endpoint": "anthropic", "url": "https://api.anthropic.com", "kind": "anthropic"},
        )
        assert len(updated) == 2
        by_name = {e["endpoint"]: e for e in updated}
        assert by_name["anthropic"]["url"] == "https://api.anthropic.com"
        assert by_name["openai"]["url"] == "https://api.openai.com"

    def test_appends_new_name(self) -> None:
        existing = [{"endpoint": "anthropic", "url": "https://api.anthropic.com"}]
        updated = merge_upstream_entry(
            existing,
            {"endpoint": "openai", "url": "https://api.openai.com", "kind": "openai"},
        )
        assert len(updated) == 2


class TestUpstreamEntryEndpoint:
    def test_reads_endpoint_key(self) -> None:
        assert upstream_entry_endpoint({"endpoint": "openrouter"}) == "openrouter"

    def test_falls_back_to_legacy_upstream_key(self) -> None:
        assert upstream_entry_endpoint({"upstream": "anthropic"}) == "anthropic"


class TestMergeEndpoints:
    def test_preserves_existing_and_appends_new(self) -> None:
        assert merge_endpoints(["anthropic"], ["openai", "anthropic"]) == [
            "anthropic",
            "openai",
        ]


class TestMergeSetupOverlay:
    def test_merges_upstreams_and_endpoints(self) -> None:
        existing = {
            "network": {
                "proxy": {
                    "reverse": {
                        "upstreams": [
                            {
                                "endpoint": "openai",
                                "kind": "openai",
                                "url": "https://api.openai.com",
                            },
                        ],
                        "endpoints": ["openai"],
                    },
                },
            },
            "stats": {"enabled": True},
        }
        overlay = build_upstream_cli_overlay(
            "https://api.anthropic.com",
            "anthropic",
        )
        merged = merge_setup_overlay(existing, overlay)
        reverse = merged["network"]["proxy"]["reverse"]
        assert reverse["upstreams"] == [
            {
                "endpoint": "openai",
                "kind": "openai",
                "url": "https://api.openai.com",
            },
            {
                "endpoint": "anthropic",
                "kind": "anthropic",
                "url": "https://api.anthropic.com",
            },
        ]
        assert reverse["endpoints"] == ["openai", "anthropic"]
        assert merged["stats"]["enabled"] is True

    def test_merges_remote_models_by_nick(self) -> None:
        existing = {
            "models": {
                "llm": {
                    "remote": [{"nick": "keep", "name": "old-model"}],
                },
            },
        }
        overlay = {
            "models": {
                "llm": {
                    "remote": [{"nick": "new", "name": "new-model"}],
                },
            },
        }
        merged = merge_setup_overlay(existing, overlay)
        nicks = {entry["nick"] for entry in merged["models"]["llm"]["remote"]}
        assert nicks == {"keep", "new"}

    def test_merges_inline_provider_fields_into_providers(self) -> None:
        existing = {
            "models": {
                "llm": {
                    "remote": [
                        {
                            "nick": "sonnet",
                            "name": "claude-sonnet-4-6",
                            "provider": "anthropic",
                            "key_var_name": "ANTHROPIC_API_KEY",
                            "domain_match": ["api.anthropic.com"],
                        },
                    ],
                },
            },
        }
        overlay = {
            "models": {
                "llm": {
                    "remote": [
                        {
                            "nick": "sonnet",
                            "name": "claude-sonnet-4-6",
                            "provider_nick": "anthropic",
                        },
                    ],
                },
                "providers": [
                    {
                        "provider_nick": "anthropic",
                        "provider": "anthropic",
                        "key_var_name": "ANTHROPIC_API_KEY",
                        "domain_match": ["api.anthropic.com"],
                    },
                ],
            },
        }
        merged = merge_setup_overlay(existing, overlay)
        remote = merged["models"]["llm"]["remote"][0]
        assert remote["provider_nick"] == "anthropic"
        assert "provider" not in remote
        assert "key_var_name" not in remote
        assert merged["models"]["providers"] == [
            {
                "provider_nick": "anthropic",
                "provider": "anthropic",
                "key_var_name": "ANTHROPIC_API_KEY",
                "domain_match": ["api.anthropic.com"],
            },
        ]


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

    def test_reads_from_provider_registry(self) -> None:
        models = {
            "providers": [
                {"provider_nick": "openrouter", "key_var_name": "OPENROUTER_API_KEY"},
                {"provider_nick": "deepinfra", "key_var_name": "DEEPINFRA_API_KEY"},
            ],
            "llm": {
                "remote": [
                    {"provider_nick": "openrouter"},
                    {"provider_nick": "openrouter"},
                ],
            },
            "rerankers": {"remote": [{"provider_nick": "deepinfra"}]},
        }
        config = {"models": models}
        assert collect_key_var_names(models, config=config) == [
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
                "",
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = _prompt_custom_model(
            prompt_base_url=True,
        )
        assert "base_url" not in result
        assert "domain_match" not in result
        assert "pricing" not in result

    def test_prompts_base_url_when_requested(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(
            [
                "deepinfra",
                "custom/reranker",
                "",
                "DEEPINFRA_API_KEY",
                "32000",
                "https://api.deepinfra.com/v1",
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        result = _prompt_custom_model(
            default_base_url="https://api.anthropic.com",
            prompt_base_url=True,
        )
        assert result["base_url"] == "https://api.deepinfra.com/v1"
        assert "domain_match" not in result
        assert "pricing" not in result


class TestCatalogProviderMerge:
    def test_rerank_catalog_entry_inherits_provider(self) -> None:
        merge_config = _catalog_merge_config()
        rerank = next(
            entry
            for entry in _catalog_entries("rerankers")
            if entry.get("nick") == "rerank-qwen3-8b"
        )
        merged = merge_model_entry(merge_config, rerank)
        assert merged["provider"] == "deepinfra"
        assert merged["key_var_name"] == "DEEPINFRA_API_KEY"


class TestPromptKeyVarName:
    def test_accepts_catalog_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert _prompt_key_var_name(default="OPENROUTER_API_KEY") == "OPENROUTER_API_KEY"

    def test_reprompts_until_non_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(["", "  ", "CUSTOM_API_KEY"])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        assert _prompt_key_var_name(provider="") == "CUSTOM_API_KEY"

    def test_infers_default_from_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: "")
        assert _prompt_key_var_name(provider="deepinfra") == "DEEPINFRA_API_KEY"


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
            minimum_tools=50,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://openrouter.ai/api",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        assert overlay["pruning"]["tools"]["sequence"] == ["rerank"]
        assert overlay["pruning"]["tools"]["pipelines"]["rerank"]["model_nick"] == "rerank-qwen3-8b"
        assert "llm" not in overlay["pruning"]["tools"]["pipelines"]
        assert overlay["pruning"]["tools"]["policy"]["system_tool"] == "prune_optional"
        assert overlay["pruning"]["tools"]["policy"]["mcp_tool"] == "prune_all"
        assert overlay["pruning"]["tools"]["policy"]["minimum_tools"] == 50
        assert overlay["defaults"]["reranking_enabled"] is True
        assert overlay["models"]["llm"]["remote"] == []
        rerank_remote = overlay["models"]["rerankers"]["remote"][0]
        assert rerank_remote["provider_nick"] == "deepinfra"
        assert "key_var_name" not in rerank_remote
        deepinfra_provider = next(
            provider
            for provider in overlay["models"]["providers"]
            if provider["provider_nick"] == "deepinfra"
        )
        assert deepinfra_provider["key_var_name"] == "DEEPINFRA_API_KEY"
        saved_upstream = overlay["network"]["proxy"]["reverse"]["upstreams"][0]
        assert saved_upstream == {
            "endpoint": "anthropic",
            "url": "https://openrouter.ai/api",
            "kind": "anthropic",
        }

    def test_bm25_only_pipeline(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["bm25"],
            reranker_model=None,
            llm_pruner_model=None,
            minimum_tools=50,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://api.anthropic.com",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        assert overlay["pruning"]["tools"]["sequence"] == ["bm25"]
        assert overlay["pruning"]["tools"]["policy"]["mcp_tool"] == "prune_all"
        assert "rerank" not in overlay["pruning"]["tools"]["pipelines"]
        assert overlay["defaults"] == {}
        assert "rerankers" not in overlay["models"]

    def test_both_pipeline_includes_pruner(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["rerank", "llm"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=_LLM_PRUNER,
            minimum_tools=50,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://openrouter.ai/api",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        nicks = {e["nick"] for e in overlay["models"]["llm"]["remote"]}
        assert nicks == {"mercury-2"}
        provider_nicks = {provider["provider_nick"] for provider in overlay["models"]["providers"]}
        assert provider_nicks == {"openrouter", "deepinfra"}
        assert overlay["pruning"]["tools"]["pipelines"]["rerank"]["model_nick"] == "rerank-qwen3-8b"
        assert overlay["pruning"]["tools"]["pipelines"]["llm"]["model_nick"] == "mercury-2"

    def test_setup_overlay_writes_only_pruner_llm_models(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["rerank", "llm"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=_LLM_PRUNER,
            minimum_tools=50,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://api.anthropic.com",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
        )
        llm_nicks = [entry["nick"] for entry in overlay["models"]["llm"]["remote"]]
        assert llm_nicks == ["mercury-2"]

    def test_skills_overlay(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["bm25"],
            reranker_model=None,
            llm_pruner_model=None,
            minimum_tools=None,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://api.anthropic.com",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
            skills={"enabled": True, "pipeline": "llm"},
        )
        assert overlay["skills"] == {"enabled": True, "pipeline": "llm"}

    def test_skills_rerank_model_writes_pipeline_without_tool_rerank(self) -> None:
        overlay = build_setup_overlay(
            pipeline=["bm25"],
            reranker_model=_RERANK_MODEL,
            llm_pruner_model=None,
            minimum_tools=50,
            system_tool_policy="prune_optional",
            mcp_tool_policy="prune_all",
            reverse_port=8834,
            upstreams=[
                {
                    "endpoint": "anthropic",
                    "url": "https://api.anthropic.com",
                    "kind": "anthropic",
                },
            ],
            endpoints=["anthropic"],
            stats_db_path="~/.config/cyt/stats.db",
            skills={"enabled": True, "pipeline": "rerank"},
        )
        assert overlay["pruning"]["tools"]["sequence"] == ["bm25"]
        assert overlay["pruning"]["tools"]["pipelines"]["rerank"]["model_nick"] == "rerank-qwen3-8b"
        assert overlay["models"]["rerankers"]["remote"][0]["nick"] == "rerank-qwen3-8b"


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
                    "  tools:",
                    "    policy:",
                    "      per_tool:",
                    "        Agent: prune_optional",
                    "    sequence:",
                    "    - rerank",
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
            {"pruning": {"tools": {"sequence": ["rerank"]}}},
            apply_bundled_sections=True,
        )
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        bundled = bundled_user_config_sections()
        loaded_per_tool = (
            loaded.get("pruning", {}).get("tools", {}).get("policy", {}).get("per_tool")
        )
        bundled_per_tool = (
            bundled.get("pruning", {}).get("tools", {}).get("policy", {}).get("per_tool")
        )
        assert loaded_per_tool == bundled_per_tool
        assert "Agent" not in loaded_per_tool
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
        assert "http://127.0.0.1:8834/anthropic" in out
        assert "http://127.0.0.1:8834/openai" in out

    def test_default_local_host(self, capsys: pytest.CaptureFixture[str]) -> None:
        print_proxy_urls(8834, ["openrouter"])
        out = capsys.readouterr().out
        assert out.strip() == "http://127.0.0.1:8834/openrouter"


class TestPromptUpstreams:
    def test_skips_full_upstream_flow_when_existing_and_declined(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        existing = {
            "network": {
                "proxy": {
                    "reverse": {
                        "upstreams": [
                            {
                                "endpoint": "anthropic",
                                "url": "https://api.anthropic.com",
                            },
                        ],
                        "endpoints": ["anthropic"],
                    },
                },
            },
            "models": {
                "llm": {
                    "remote": [_SAMPLE_MODEL],
                },
            },
        }
        prompts: list[str] = []

        def capture_input(prompt: str) -> str:
            prompts.append(prompt)
            return "n"

        monkeypatch.setattr("builtins.input", capture_input)
        upstreams, endpoints = _prompt_upstreams(existing)
        out = capsys.readouterr().out
        assert len(upstreams) == 1
        assert endpoints == ["anthropic"]
        assert "Configured upstreams:" in out
        assert "endpoint" in out and "kind" in out and "url" in out
        assert "anthropic  anthropic  https://api.anthropic.com" in out
        assert any("Add another upstream?" in p for p in prompts)
        assert not any("Upstream kind" in p for p in prompts)


class TestPromptSkills:
    def test_enable_and_derive_pipeline_from_tool_pruning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        responses = iter(["y", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        skills = _prompt_skills({}, tool_pipeline=["rerank"])
        assert skills == {
            "enabled": True,
            "pipeline": "rerank",
            "directories": [
                "~/.claude/skills",
                ".claude/skills",
                "~/.codex/skills",
                ".codex/skills",
                ".cursor/skills",
                "~/.cursor/skills",
            ],
        }

    def test_defaults_to_bm25_pipeline_without_tool_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        responses = iter(["y", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        skills = _prompt_skills({})
        assert skills["pipeline"] == "bm25"
        assert "inject_via" not in skills

    def test_defaults_to_tool_pruning_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        responses = iter(["y", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        skills = _prompt_skills({}, tool_pipeline=["rerank"])
        assert skills["pipeline"] == "rerank"
        assert "inject_via" not in skills

    def test_tool_pipeline_overrides_existing_skills_pipeline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        responses = iter(["y", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        skills = _prompt_skills(
            {"skills": {"enabled": True, "pipeline": "llm"}},
            tool_pipeline=["rerank"],
        )
        assert skills["pipeline"] == "rerank"


class TestPipelineLabels:
    def test_bm25_label_uses_minimum_tools(self) -> None:
        from cyt.proxy.setup_wizard import _pipeline_choice_labels, _pipeline_from_display_label

        labels = _pipeline_choice_labels(0, minimum_tools=42)
        assert labels[3] == "bm25 (no API key, local; Defaults to when below 42 tools)"
        assert _pipeline_from_display_label(labels[3]) == ["bm25"]
        assert _pipeline_from_display_label(
            "bm25 (no API key, local; Defaults to when below 42 tools) (recommended)",
        ) == [
            "bm25",
        ]

    def test_disable_skips_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        skills = _prompt_skills({"skills": {"enabled": True, "pipeline": "llm"}})
        assert skills == {"enabled": False}


class TestRunSetupKeyring:
    def test_skips_env_prompt_when_keyring_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {
                    "network": {
                        "proxy": {
                            "reverse": {
                                "port": 8834,
                                "upstreams": [
                                    {
                                        "endpoint": "anthropic",
                                        "url": "https://api.anthropic.com",
                                    },
                                ],
                                "endpoints": ["anthropic"],
                            },
                        },
                    },
                    "models": {
                        "llm": {
                            "remote": [_SAMPLE_MODEL],
                        },
                    },
                },
            ),
            encoding="utf-8",
        )
        responses = iter(
            [
                "",  # port default
                "n",  # add another upstream
                "",  # minimum_tools default
                "",  # system policy default
                "",  # mcp policy default
                "",  # inject_via default (proxy)
                "n",  # skills disabled
                "",  # stats db default
            ],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))
        monkeypatch.setattr("cyt.launch.secrets.keyring_backend_available", lambda: True)
        monkeypatch.setattr("cyt.proxy.setup_wizard._prompt_inject_via", lambda _e: "proxy")
        monkeypatch.setattr("cyt.proxy.setup_wizard.save_user_config", lambda *_a, **_k: False)
        monkeypatch.setattr(
            "cyt.proxy.setup_wizard._prompt_primary_model_input_cost",
            lambda: {"pricing": {"input_cost_per_token": 3e-06}},
        )
        monkeypatch.setattr("cyt.proxy.setup_wizard._prompt_pipeline", lambda **_k: ["bm25"])
        run_setup(config_path)
        out = capsys.readouterr().out
        assert "OS keyring is available" in out
        assert "Create a .env file for API keys?" not in out
