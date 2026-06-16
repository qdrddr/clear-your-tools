#!/usr/bin/env python3
"""Tests for LLM catalog pruner LiteLLM routing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cyt.common.token_usage import empty_usage
from cyt.pruners.llm import LlmPruningSettings, RelevantChunkIds, call_llm, llm_select_ids


def _settings(*, responses_api: bool) -> LlmPruningSettings:
    return LlmPruningSettings(
        model_name="openai/gpt-5.5",
        api_key="1111111112222222222222222222222222222222",
        base_url=None,
        provider="openai",
        provider_dns="api.openai.com",
        responses_api=responses_api,
    )


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
    assert responses_mock.call_args.kwargs["input"].startswith("User Query: find tools")
    assert parsed.ids == [7, 8]
    assert usage.input_tokens == 12
    assert usage.output_tokens == 4
    assert usage.usage_source == "provider"


def test_call_llm_none_content_includes_response_diagnostics() -> None:
    fake_response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=None),
                finish_reason="length",
            ),
        ],
        usage=SimpleNamespace(
            completion_tokens=0,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        id="chatcmpl-bad",
    )

    with patch("cyt.pruners.llm.completion", return_value=fake_response):
        with pytest.raises(ValueError, match=r"model='openai/gpt-5\.5'") as exc_info:
            call_llm(_settings(responses_api=False), "find tools", "<chunk>")

    message = str(exc_info.value)
    assert "finish_reason='length'" in message
    assert "content_type=NoneType" in message
    assert "completion_tokens=0" in message
    assert "reasoning_tokens=0" in message
