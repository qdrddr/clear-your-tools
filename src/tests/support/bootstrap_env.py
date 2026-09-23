"""Test environment defaults loaded before any ``cyt`` imports."""

from __future__ import annotations

import os

os.environ.setdefault("CYT_NO_AUTO_BOOTSTRAP", "1")
