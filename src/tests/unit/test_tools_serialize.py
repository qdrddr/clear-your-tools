"""Tests for hook-only JSON serialization."""

from __future__ import annotations

from cyt.tools.serialize import (
    canonicalize_json_value,
    format_example_line,
    format_examples_block,
    minimize_json_single_quotes,
)


def test_minimize_json_single_quotes_example() -> None:
    value = {"a": 1, "b": 'hello "world"', "c": {"nested": "value"}}
    text = minimize_json_single_quotes(value)
    assert text == "{'a':1,'b':'hello \"world\"','c':{'nested':'value'}}"


def test_minimize_json_preserves_inner_apostrophe() -> None:
    assert minimize_json_single_quotes("it's fine") == "'it's fine'"


def test_minimize_json_preserves_escaped_quotes_in_string() -> None:
    assert minimize_json_single_quotes('hello "world"') == "'hello \"world\"'"


def test_minimize_json_empty_structures() -> None:
    assert minimize_json_single_quotes({}) == "{}"
    assert minimize_json_single_quotes([]) == "[]"
    assert minimize_json_single_quotes("") == "''"


def test_format_examples_block_omits_tag_when_empty() -> None:
    assert format_examples_block([]) == ""
    assert format_examples_block([{}]) == ""
    assert format_examples_block([{}, {"source": "docs"}]) == (
        "<examples>\n- {'source':'docs'}\n</examples>"
    )


def test_format_example_line_sorts_keys_canonically() -> None:
    line = format_example_line({"repo": "/tmp", "query": "BM25 ranking score"})
    assert line == "- {'query':'BM25 ranking score','repo':'/tmp'}"


def test_canonicalize_json_value_sorts_nested_object_keys() -> None:
    value = {"z": {"b": 2, "a": 1}, "a": 1}
    assert canonicalize_json_value(value) == {"a": 1, "z": {"a": 1, "b": 2}}


def test_format_examples_block_uses_single_quotes() -> None:
    block = format_examples_block([{"path": "/tmp/spec.md", "source": "openapi-v2-spec"}])
    assert block == (
        "<examples>\n- {'path':'/tmp/spec.md','source':'openapi-v2-spec'}\n</examples>"
    )


def test_format_examples_block_zero_max_chars_disables_truncation() -> None:
    long_repo = "/Volumes/OWCExpress1M2/Users/dberezenko/git/github.com/qdrddr/clear-your-tools"
    block = format_examples_block(
        [{"query": "BM25 ranking score", "repo": long_repo}],
        max_chars=0,
    )
    assert long_repo in block
    assert "..." not in block
