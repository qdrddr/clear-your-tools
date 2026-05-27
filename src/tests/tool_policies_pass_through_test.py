"""Tests for full pass-through when both tool policies are always_include."""

from __future__ import annotations

import pytest

from tool_policies import MCPToolPolicy, SystemToolPolicy, full_pass_through


class TestFullPassThroughPredicate:
    def test_both_always_include(self) -> None:
        assert full_pass_through("always_include", "always_include") is True

    @pytest.mark.parametrize(
        ("system_policy", "mcp_policy"),
        [
            ("prune_optional", "always_include"),
            ("always_include", "prune_optional"),
            ("prune_all", "always_include"),
            ("always_include", "prune_all"),
            ("prune_optional", "prune_optional"),
        ],
    )
    def test_mixed_policies_false(
        self,
        system_policy: SystemToolPolicy,
        mcp_policy: MCPToolPolicy,
    ) -> None:
        assert full_pass_through(system_policy, mcp_policy) is False
