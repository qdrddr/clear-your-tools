/** In-memory decomposed catalog (Rust-backed). */

import { DecomposedCatalogNative } from "./native.js";

export type DecomposedCatalog = InstanceType<typeof DecomposedCatalogNative>;
export const DecomposedCatalog = DecomposedCatalogNative;
