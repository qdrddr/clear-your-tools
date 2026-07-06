"""Agent hook skills injection and session tracking."""

from __future__ import annotations


def run(
    debug: bool = False,
    prompt: str | None = None,
    model: str | None = None,
    test: bool = False,
) -> None:
    from cyt.skills.cli import run as _run

    _run(debug=debug, prompt=prompt, model=model, test=test)


__all__ = ["run"]
