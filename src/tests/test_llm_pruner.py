#!/usr/bin/env python3
"""Tests for LLM catalog pruner LiteLLM routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from cyt.common.token_usage import empty_usage
from cyt.pruners.llm import (
    TOOL_SELECTOR_SYSTEM_PROMPT,
    LlmPruningSettings,
    RelevantChunkIds,
    _llm_user_message,
    call_llm,
    llm_select_ids,
    normalize_selector_ids,
    tool_selector_system_prompt,
)
from cyt.tools.sources.executor_mcp import format_executor_mcp_selector_appendix


def _settings(*, responses_api: bool) -> LlmPruningSettings:
    return LlmPruningSettings(
        model_name="openai/gpt-5.5",
        api_key="1111111112222222222222222222222222222222",
        base_url=None,
        provider="openai",
        provider_dns="api.openai.com",
        responses_api=responses_api,
    )


def test_llm_user_message_puts_query_after_chunks() -> None:
    message = _llm_user_message("find tools", "<chunk id=1>\n{}\n</chunk>")
    assert message.startswith("Available Chunks:\n\n")
    assert message.endswith("</user-query>")
    assert "<chunk id=1>" in message
    assert "<user-query >\nfind tools\n</user-query>" in message


def test_call_llm_uses_custom_system_prompt() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[3]}'))],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=2),
        id="chatcmpl-custom",
    )
    custom_prompt = "Select relevant skill-node ids only."

    with patch("cyt.pruners.llm.completion", return_value=fake_response) as completion_mock:
        parsed, _usage = call_llm(
            _settings(responses_api=False),
            "find skills",
            "<skill-node>",
            system_prompt=custom_prompt,
        )

    assert parsed.ids == [3]
    messages = completion_mock.call_args.kwargs["messages"]
    assert messages[0]["content"] == custom_prompt


def test_llm_select_ids_unions_bulk_ids() -> None:
    with patch(
        "cyt.pruners.llm.call_llm",
        side_effect=[
            (RelevantChunkIds(ids=[1, 2]), empty_usage()),
            (RelevantChunkIds(ids=[3]), empty_usage()),
        ],
    ):
        with patch(
            "cyt.pruners.split.split_chunks_into_bulks",
            return_value=["bulk-a", "bulk-b"],
        ):
            with patch("cyt.pruners.llm.llm_pruning_settings") as settings_mock:
                settings_mock.return_value = _settings(responses_api=False)
                selected, _usage = llm_select_ids("query", "prompt", ["a", "b"])

    assert selected == {1, 2, 3}


def test_call_llm_uses_completion_for_chat_completions() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[1,2]}'))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-test",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response) as completion_mock:
        with patch("cyt.pruners.llm.responses") as responses_mock:
            parsed, usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    completion_mock.assert_called_once()
    responses_mock.assert_not_called()
    assert completion_mock.call_args.kwargs["response_format"].__name__ == "RelevantChunkIds"
    assert parsed.ids == [1, 2]
    assert usage.input_tokens == 10
    assert usage.output_tokens == 3


def test_call_llm_uses_responses_api_when_enabled() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[7,8]}'))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=4),
        id="resp-test",
    )

    with patch("cyt.pruners.llm.responses", return_value=fake_response) as responses_mock:
        with patch("cyt.pruners.llm.completion") as completion_mock:
            parsed, usage = call_llm(_settings(responses_api=True), "find tools", "<chunk>")

    responses_mock.assert_called_once()
    completion_mock.assert_not_called()
    assert responses_mock.call_args.kwargs["text_format"].__name__ == "RelevantChunkIds"
    assert responses_mock.call_args.kwargs["instructions"]
    assert responses_mock.call_args.kwargs["input"].startswith("Available Chunks:")
    assert "<user-query >\nfind tools\n</user-query>" in responses_mock.call_args.kwargs["input"]
    assert responses_mock.call_args.kwargs["input"].endswith("</user-query>")
    assert parsed.ids == [7, 8]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.usage_source == "provider"


def test_call_llm_none_content_returns_zero_selections() -> None:
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None),
                finish_reason="stop",
            ),
        ],
        usage=SimpleNamespace(
            completion_tokens=477,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=408),
        ),
        id="chatcmpl-bad",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        parsed, _usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    assert parsed.ids == []


def test_call_llm_ignores_reasoning_only_response() -> None:
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    reasoning_content='{"ids":[4,5]}',
                ),
                finish_reason="stop",
            ),
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-reasoning",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        parsed, _usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    assert parsed.ids == []


def test_call_llm_invalid_structured_content_returns_zero_selections() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-invalid",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        parsed, _usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    assert parsed.ids == []


def test_call_llm_openrouter_requests_no_reasoning_effort() -> None:
    settings = LlmPruningSettings(
        model_name="openrouter/inception/mercury-2",
        api_key="1111111112222222222222222222222222222222",
        base_url="https://openrouter.ai/api",
        provider="openrouter",
        provider_dns="openrouter",
        responses_api=False,
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[1]}'))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-mercury",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response) as completion_mock:
        call_llm(settings, "find tools", "<chunk>")

    assert completion_mock.call_args.kwargs["reasoning"] == {"effort": "none"}


def test_call_llm_openrouter_gpt_oss_requests_low_reasoning_effort() -> None:
    settings = LlmPruningSettings(
        model_name="openrouter/openai/gpt-oss-120b",
        api_key="1111111112222222222222222222222222222222",
        base_url="https://openrouter.ai/api",
        provider="openrouter",
        provider_dns="openrouter",
        responses_api=False,
    )
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[1]}'))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-gpt-oss",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response) as completion_mock:
        call_llm(settings, "find tools", "<chunk>")

    assert completion_mock.call_args.kwargs["reasoning"] == {"effort": "low"}


def test_normalize_selector_ids_drops_empty_sentinel() -> None:
    assert normalize_selector_ids([-1]) == []
    assert normalize_selector_ids([1, -1, 3]) == [1, 3]
    assert normalize_selector_ids([]) == []


def test_call_llm_minus_one_means_zero_selections() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[-1]}'))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-empty",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        parsed, _usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    assert parsed.ids == []


def test_call_llm_minus_one_mixed_with_valid_ids() -> None:
    fake_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ids":[2,-1,5]}'))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3),
        id="chatcmpl-mixed",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        parsed, _usage = call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    assert parsed.ids == [2, 5]


def test_format_executor_mcp_selector_appendix_minimizes_execute_tool() -> None:
    appendix = format_executor_mcp_selector_appendix(
        {
            "tools_list": [
                {
                    "name": "execute",
                    "description": 'Run "code"',
                    "inputSchema": {
                        "type": "object",
                        "properties": {"code": {"type": "string"}},
                        "required": ["code"],
                    },
                },
            ],
            "execute_skill": "# execute\n\nUse tools.search()",
        },
    )
    assert "Executor MCP transport context" in appendix
    assert "<tool name='execute'" in appendix
    assert "description='Run \"code\"'" in appendix
    assert "{'input_schema':{" in appendix
    assert "<execute-skill>" in appendix
    assert "Use tools.search()" in appendix
    assert "</execute-skill>" in appendix


def test_tool_selector_system_prompt_appends_cached_mcp() -> None:
    mcp = {
        "tools_list": [{"name": "execute", "description": "Run", "inputSchema": {}}],
        "execute_skill": "# execute",
    }
    with patch(
        "cyt.tools.sources.executor_http.get_executor_mcp_cache",
        return_value=mcp,
    ):
        prompt = tool_selector_system_prompt({"pruning": {}})

    assert prompt.startswith(TOOL_SELECTOR_SYSTEM_PROMPT)
    assert "<tool name='execute'" in prompt
    assert "# execute" in prompt


def test_tool_selector_system_prompt_without_cache_is_base_only() -> None:
    with patch(
        "cyt.tools.sources.executor_http.get_executor_mcp_cache",
        return_value=None,
    ):
        assert tool_selector_system_prompt() == TOOL_SELECTOR_SYSTEM_PROMPT


def test_trim_catalog_dict_keeps_top_k_by_score() -> None:
    from cyt.pruners.llm import trim_catalog_dict

    data = {
        "json": [
            {"file_path": "a", "score": 0.9},
            {"file_path": "b", "score": 0.5},
            {"file_path": "c", "score": 0.2},
            {"file_path": "d", "score": 0.05},
        ],
    }
    trimmed = trim_catalog_dict(data, top_k=2)
    assert [item["file_path"] for item in trimmed["json"]] == ["a", "b"]
