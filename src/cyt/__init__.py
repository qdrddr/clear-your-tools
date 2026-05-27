"""Clear Your Tools (CYT) — dynamic tool gating for LLM agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("clear-your-tools")
except PackageNotFoundError:
    __version__ = "0.0.0"  # editable install / dev checkout
