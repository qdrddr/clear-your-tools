/** Catalog disk I/O and builder (Rust-backed). */

import { CatalogBuilderNative, writeCatalogIndexNative } from "./native.js";
import type { JsonRecord } from "./types.js";

export function writeCatalogIndex(
  index: JsonRecord,
  outputDir?: string | null,
  prune?: boolean | null,
): void {
  writeCatalogIndexNative(index, outputDir ?? undefined, prune ?? undefined);
}

export type CatalogBuilder = InstanceType<typeof CatalogBuilderNative>;

export { CatalogBuilderNative as CatalogBuilder };
