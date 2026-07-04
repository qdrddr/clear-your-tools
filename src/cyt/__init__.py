"""Clear Your Tools (CYT) — dynamic tool gating for LLM agents."""

from __future__ import annotations

import warnings
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

try:
    __version__ = version("clear-your-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"

if TYPE_CHECKING:
    from cyt_core.bootstrap import AppContext, bootstrap

__all__ = ["AppContext", "__version__", "bootstrap"]

_cyt_bootstrapped = False


def __getattr__(name: str) -> object:
    if name == "AppContext":
        from cyt_core.bootstrap import AppContext

        return AppContext
    if name == "bootstrap":
        from cyt_core.bootstrap import bootstrap

        return bootstrap
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _compat_bootstrap() -> None:
    global _cyt_bootstrapped
    if _cyt_bootstrapped:
        return
    _cyt_bootstrapped = True
    warnings.warn(
        "Importing cyt runs bootstrap side effects on import; prefer "
        "`from cyt_core import bootstrap; bootstrap()` for explicit initialization.",
        DeprecationWarning,
        stacklevel=2,
    )
    from cyt_core.bootstrap import bootstrap as _bootstrap

    try:
        from cyt.config import load_config

        _bootstrap(config=load_config())
    except Exception:
        _bootstrap()


_compat_bootstrap()
