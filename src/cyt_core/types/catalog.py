"""Typed catalog snapshots for pipeline APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cyt_indexer.build import CatalogIndex, NativeCatalogIndex


@dataclass(frozen=True)
class CatalogSnapshot:
    """In-memory tool catalog state passed to composite pipeline APIs."""

    catalog_data: dict[str, Any]
    build_catalog: dict[str, Any]
    catalog_index: CatalogIndex | NativeCatalogIndex

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
            catalog_index=index.native_handle(),
        )

    def pipeline_catalog_index(self) -> CatalogIndex | NativeCatalogIndex:
        if isinstance(self.catalog_index, NativeCatalogIndex):
            return self.catalog_index
        if isinstance(self.catalog_index, CatalogIndex):
            return self.catalog_index.native_handle()
        return self.catalog_index


__all__ = ["CatalogSnapshot"]
