"""Verify automated test runs cannot spawn cyt hook daemon or launch proxy."""

from __future__ import annotations

import pytest

from cyt.hook import daemon as hook_daemon


def test_spawn_hook_server_blocked_in_automated_runs() -> None:
    with pytest.raises(pytest.fail.Exception, match="hook daemon / launch proxy spawn blocked"):
        hook_daemon._spawn_hook_server(
            port=8834,
            config_path=None,
            verbose=False,
        )
