"""Typed catalog snapshots for pipeline APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt_indexer.build import CatalogIndex


@dataclass(frozen=True)
class CatalogSnapshot:
    """In-memory tool catalog state passed to composite pipeline APIs."""

    catalog_data: dict[str, Any]
    build_catalog: dict[str, Any]
    catalog_index: dict[str, Any]

    @classmethod
    def from_index(
        cls,
        catalog_data: dict[str, Any],
        build_catalog: dict[str, Any],
        index: CatalogIndex,
    ) -> CatalogSnapshot:
        return cls(
            catalog_data=catalog_data,
            build_catalog=build_catalog,
            catalog_index={"tools": index.tools, "files": index.files},
        )


__all__ = ["CatalogSnapshot"]
