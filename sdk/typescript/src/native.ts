import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

const native = require("../native.cjs") as typeof import("../native.d.ts");

export const {
  buildCatalogIndex,
  catalogToolCount,
  compactJson,
  countJsonTokens,
  countTokens,
  loadCatalog,
  retrieveCore,
} = native;

export type { CatalogIndexResult, PolicyOptions } from "../native.d.ts";
