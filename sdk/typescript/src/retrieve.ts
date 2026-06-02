import type { CatalogIndex } from "./build.js";
import {
  DecomposedCatalog,
  resolveDecomposedCatalog,
} from "./decomposed-catalog.js";
import {
  loadCatalogNative,
  retrieveCoreNative,
  type PolicyOptions,
} from "./native.js";
import { isJsonRecord, type JsonRecord } from "./types.js";

export const DECOMPOSED_SCORE = 0.5;
export const ENUM_SCORE = 0.2;

export type { PolicyOptions };
export type { JsonRecord };
export { DecomposedCatalog };

export function loadCatalog(dirPath: string): {
  md: JsonRecord[];
  json: JsonRecord[];
  tools: JsonRecord[];
} {
  return loadCatalogNative(dirPath) as {
    md: JsonRecord[];
    json: JsonRecord[];
    tools: JsonRecord[];
  };
}

export interface RetrieveToolsOptions {
  catalog: DecomposedCatalog | CatalogIndex;
  applyDecomposedScoreFilter?: boolean;
  policyOptions?: PolicyOptions | null;
}

export function retrieveTools(
  data: unknown,
  options: RetrieveToolsOptions,
): JsonRecord[] {
  const {
    catalog,
    applyDecomposedScoreFilter = true,
    policyOptions = null,
  } = options;

  const store = resolveDecomposedCatalog(catalog);
  const catalogDict = isJsonRecord(data) ? data : {};
  const survivorStore = DecomposedCatalog.fromCatalogDict(catalogDict);

  const result = retrieveCoreNative(
    catalogDict,
    store.toJsonFiles(),
    survivorStore.toJsonFiles(),
    applyDecomposedScoreFilter,
    policyOptions ?? undefined,
  );

  return result as JsonRecord[];
}
