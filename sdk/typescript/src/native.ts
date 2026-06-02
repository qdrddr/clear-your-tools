import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const native = require("../native.cjs") as typeof import("../native.d.ts");

export const buildCatalogIndexNative = native.buildCatalogIndex;
export const catalogToolCountNative = native.catalogToolCount;
export const loadCatalogNative = native.loadCatalog;
export const retrieveCoreNative = native.retrieveCore;

export type { PolicyOptions } from "../native.d.ts";
