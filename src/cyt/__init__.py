"""Clear Your Tools (CYT) — dynamic tool gating for LLM agents."""

from importlib.metadata import PackageNotFoundError, version

from cyt.common.bm25_constants import configure_sdk_bm25_defaults
from cyt.common.path_constants import configure_sdk_path_constants
from cyt.common.runtime_constants import configure_sdk_runtime_defaults
from cyt.common.tokenizer_constants import configure_sdk_tokenizer_defaults

configure_sdk_path_constants()
configure_sdk_runtime_defaults()
configure_sdk_tokenizer_defaults()
configure_sdk_bm25_defaults()


try:
    __version__ = version("clear-your-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"  # editable install / dev checkout
