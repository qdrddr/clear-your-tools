import { CatalogIndex } from "./build.js";
import { DecomposedCatalog } from "./decomposed-catalog.js";
import {
  loadCatalogNative,
  retrieveCoreNative,
  retrieveToolsNative,
  type PolicyContextJs,
  type PolicyOptions,
} from "./native.js";
import { PolicyContext } from "./policies.js";
import { isJsonRecord, type JsonRecord } from "./types.js";

export type { PolicyOptions, PolicyContextJs };
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
  preserveValues?: Iterable<string> | null;
  ctx?: InstanceType<typeof PolicyContext> | PolicyContextJs | null;
  /** @deprecated Use ctx; kept for low-level retrieve_core passthrough */
  policyOptions?: PolicyOptions | null;
}

function catalogToNative(
  catalog: DecomposedCatalog | CatalogIndex,
): JsonRecord {
  if (catalog instanceof CatalogIndex) {
    return { tools: catalog.tools, files: catalog.files };
  }
  if (catalog instanceof DecomposedCatalog) {
    return catalog.toJsonFiles() as JsonRecord;
  }
  return catalog as JsonRecord;
}

function storeJsonFilesFromCatalog(
  catalog: DecomposedCatalog | CatalogIndex,
): Record<string, unknown> {
  if (catalog instanceof CatalogIndex) {
    return catalog.files;
  }
  if (catalog instanceof DecomposedCatalog) {
    return catalog.toJsonFiles();
  }
  return {};
}

function retrieveToolsViaPolicyOptions(
  catalogDict: JsonRecord,
  catalog: DecomposedCatalog | CatalogIndex,
  applyDecomposedScoreFilter: boolean,
  policyOptions: PolicyOptions,
): JsonRecord[] {
  const survivor = DecomposedCatalog.fromCatalogDict(catalogDict);
  return retrieveCoreNative(
    catalogDict,
    storeJsonFilesFromCatalog(catalog),
    survivor.toJsonFiles(),
    applyDecomposedScoreFilter,
    policyOptions,
  ) as JsonRecord[];
}

function retrieveToolsViaContext(
  catalogDict: JsonRecord,
  catalog: DecomposedCatalog | CatalogIndex,
  applyDecomposedScoreFilter: boolean,
  preserveValues: Iterable<string> | null | undefined,
  ctx: InstanceType<typeof PolicyContext> | PolicyContextJs | null,
): JsonRecord[] {
  const preserveList =
    preserveValues == null ? undefined : [...preserveValues].sort();
  return retrieveToolsNative(
    catalogDict,
    catalogToNative(catalog),
    applyDecomposedScoreFilter,
    preserveList,
    ctx ?? undefined,
  ) as JsonRecord[];
}

export function retrieveTools(
  data: unknown,
  options: RetrieveToolsOptions,
): JsonRecord[] {
  const {
    catalog,
    applyDecomposedScoreFilter = true,
    preserveValues,
    ctx = null,
    policyOptions = null,
  } = options;

  const catalogDict = isJsonRecord(data) ? data : {};
  if (policyOptions != null && ctx == null) {
    return retrieveToolsViaPolicyOptions(
      catalogDict,
      catalog,
      applyDecomposedScoreFilter,
      policyOptions,
    );
  }
  return retrieveToolsViaContext(
    catalogDict,
    catalog,
    applyDecomposedScoreFilter,
    preserveValues,
    ctx,
  );
}
