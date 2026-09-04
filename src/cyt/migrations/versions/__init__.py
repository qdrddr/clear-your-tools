"""Revision modules (001_*.py …)."""

from __future__ import annotations

import importlib
from typing import cast

from cyt.migrations.base import RevisionModule

_REVISION_MODULE_NAMES = (
    "001_add_schema_version",
    "002_pruning_tools_namespace",
    "003_model_provider_registry",
    "004_permissions_agents_layout",
)


def load_revision_modules() -> list[RevisionModule]:
    modules: list[RevisionModule] = []
    for name in _REVISION_MODULE_NAMES:
        module = importlib.import_module(f"cyt.migrations.versions.{name}")
        modules.append(cast(RevisionModule, module))
    return modules
