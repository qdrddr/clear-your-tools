"""MCP aggregator, relay server, and discovery utilities."""
import sys
from pathlib import Path
_SRC_ROOT = Path(__file__).resolve().parent.parent
_src_root_str = str(_SRC_ROOT)
if _src_root_str not in sys.path:
    sys.path.insert(0, _src_root_str)