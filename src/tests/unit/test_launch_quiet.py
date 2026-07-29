"""Tests for launch quiet configuration."""

from __future__ import annotations

import logging

from cyt.launch import quiet as launch_quiet


def test_configure_launch_quiet_disables_logging() -> None:
    launch_quiet._configured = False
    launch_quiet.configure_launch_quiet()
    assert logging.getLogger().manager.disable >= logging.CRITICAL

    launch_quiet._configured = False
