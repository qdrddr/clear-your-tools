"""Tests for hook-only JSON serialization."""

from __future__ import annotations

from cyt.tools.serialize import minimize_json_single_quotes


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
