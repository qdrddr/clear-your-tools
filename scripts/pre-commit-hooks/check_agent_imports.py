#!/usr/bin/env python3
"""Smoke-import orchestrators and agent registry in one process."""

from __future__ import annotations


def main() -> int:
    import cyt.agents
    from cyt.agents._registry import get_agent

    for name in ("claude", "codex", "cursor"):
        cap = get_agent(name)
        assert cap.name == name

    import cyt.proxy.reverse
    import cyt.skills.cli  # noqa: F401

    print("agent import smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
