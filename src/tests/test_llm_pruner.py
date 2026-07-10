#!/usr/bin/env python3
"""Tests for LLM catalog pruner LiteLLM routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from cyt.common.token_usage import StageTokenUsage, empty_usage
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


def test_prepare_catalog_selector_chunks_emits_token_attrs() -> None:
    from cyt.pruners.llm import prepare_catalog_selector_chunks

    catalog = {
        "json": [
            {
                "id": "tool-a",
                "file_path": "catalog/schemas/decomposed/tool-a.json",
                "content": {"type": "object"},
                "token_count": 123,
            },
        ],
        "md": [
            {
                "id": "enum-a",
                "file_path": "catalog/schemas/decomposed/enum-a.md",
                "content": "value",
                "token_count": 45,
            },
        ],
    }
    chunks, _metadata, _keys, token_counts = prepare_catalog_selector_chunks(catalog)
    combined = "".join(chunks)
    assert "<tool id=1 tokens=123>" in combined
    assert "<chunk id=2 tokens=45>" in combined
    assert '"token_count"' not in combined
    assert token_counts == [123, 45]


def test_split_chunks_into_bulks_wraps_agent_tools_total() -> None:
    from cyt.pruners.split import split_chunks_into_bulks

    chunks = ["<tool id=1 tokens=10>\n{}\n</tool>\n", "<chunk id=2 tokens=20>\n{}\n</chunk>\n"]
    bulks = split_chunks_into_bulks(
        "query",
        "prompt",
        chunks,
        chunk_token_counts=[10, 20],
        wrap_agent_tools=True,
        max_tokens=32000,
    )
    assert len(bulks) == 1
    assert "<agent-tools total-tokens=30>" in bulks[0]
    assert "<tool id=1 tokens=10>" in bulks[0]


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


def test_per_bulk_soft_budget_splits_evenly() -> None:
    from cyt.pruners.selector_xml import per_bulk_soft_budget

    assert per_bulk_soft_budget(5000, 1) == 5000
    assert per_bulk_soft_budget(5000, 2) == 2500
    assert per_bulk_soft_budget(5000, 51) == 100


def test_build_tool_selector_system_prompt_uses_dynamic_budget() -> None:
    from cyt.pruners.llm import build_tool_selector_system_prompt

    prompt = build_tool_selector_system_prompt(soft_budget=2500)
    assert "soft budget of 2500 tokens" in prompt
    assert "soft budget of 5000 tokens" not in prompt


def test_split_into_bulks_uses_cached_token_counts() -> None:
    from cyt.pruners.split import split_into_bulks

    with patch("cyt.pruners.split.count_tokens_batch") as count_mock:
        bulks = split_into_bulks(
            items=["chunk-a", "chunk-b"],
            transform_fn=lambda item: item,
            base_tokens=10,
            max_tokens=100,
            item_token_counts=[40, 30],
        )

    count_mock.assert_not_called()
    assert len(bulks) == 1
    assert bulks[0] == ["chunk-a", "chunk-b"]


def test_llm_select_ids_uses_per_bulk_soft_budget_in_prompt() -> None:
    captured_prompts: list[str] = []

    def _capture_call_llm(
        _settings: LlmPruningSettings,
        _query: str,
        _bulk_text: str,
        *,
        system_prompt: str,
    ) -> tuple[RelevantChunkIds, StageTokenUsage]:
        captured_prompts.append(system_prompt)
        return RelevantChunkIds(ids=[1]), empty_usage()

    with patch("cyt.pruners.llm.call_llm", side_effect=_capture_call_llm):
        with patch(
            "cyt.pruners.split.split_chunks_into_bulks",
            return_value=["bulk-a", "bulk-b"],
        ):
            with patch("cyt.pruners.llm.llm_pruning_settings") as settings_mock:
                settings_mock.return_value = _settings(responses_api=False)
                llm_select_ids(
                    "query",
                    "ignored",
                    ["a", "b"],
                    system_prompt_for_budget=lambda budget: f"budget={budget}",
                    soft_budget_total=5000,
                )

    assert captured_prompts == ["budget=2500", "budget=2500"]


def test_replace_selector_soft_budget_preserves_mcp_appendix_braces() -> None:
    from cyt.pruners.selector_xml import replace_selector_soft_budget

    prompt = (
        "You have a soft budget of 5000 tokens to select the most relevant chunks."
        "<tool name='execute'>{'input_schema':{}}</tool>"
    )
    updated = replace_selector_soft_budget(prompt, 2500)
    assert "soft budget of 2500 tokens" in updated
    assert "<tool name='execute'>" in updated


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
