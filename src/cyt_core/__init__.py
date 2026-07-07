"""Clear Your Tools core SDK (no import side effects)."""

from importlib.metadata import PackageNotFoundError, version

from cyt_core.bootstrap import (
    AppContext,
    bootstrap,
    configure_sdk_bm25_defaults,
    configure_sdk_path_constants,
    configure_sdk_runtime_defaults,
    configure_sdk_tokenizer_defaults,
)
from cyt_core.types.config import Bm25SdkConfig

try:
    __version__ = version("clear-your-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "AppContext",
    "Bm25SdkConfig",
    "__version__",
    "bootstrap",
    "configure_sdk_bm25_defaults",
    "configure_sdk_path_constants",
    "configure_sdk_runtime_defaults",
    "configure_sdk_tokenizer_defaults",
]
