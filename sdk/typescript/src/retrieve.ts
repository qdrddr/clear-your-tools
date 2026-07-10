import { CatalogIndex } from "./build.js";
import { DecomposedCatalog } from "./decomposed-catalog.js";
import {
  chunkSurvivorKeyNative,
  loadCatalogNative,
  removedChunksNative,
  resolveBuildCatalogNative,
  retrieveCatalogToolCountNative,
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

/** Catalog dict shape produced by {@link loadCatalog} and rerank survivors output. */
export interface DecomposedCatalogDict {
  json: JsonRecord[];
  md: JsonRecord[];
}

export interface RemovedChunksOptions {
  /** When true, json entries in `surviving` with score <= decomposed threshold count as removed. */
  applyDecomposedScoreFilter?: boolean;
}

/** Normalized identity for a catalog chunk (`json` or `md` array item). */
export function chunkSurvivorKey(
  item: JsonRecord,
  section: "json" | "md",
): string | null {
  return chunkSurvivorKeyNative(item, section);
}

/**
 * Decomposed chunks in `fullCatalog` that are not in `surviving` (same shape as survivors.json).
 */
export function removedChunks(
  fullCatalog: DecomposedCatalogDict,
  surviving: DecomposedCatalogDict,
  options: RemovedChunksOptions = {},
): DecomposedCatalogDict {
  const { applyDecomposedScoreFilter = false } = options;
  return removedChunksNative(
    fullCatalog,
    surviving,
    applyDecomposedScoreFilter,
  ) as DecomposedCatalogDict;
}

/** Resolve build catalog JSON from catalog index or decomposed catalog + survivor data. */
export function resolveBuildCatalog(
  catalog: JsonRecord,
  survivorData: JsonRecord,
): JsonRecord {
  return resolveBuildCatalogNative(catalog, survivorData) as JsonRecord;
}

/** Count tools in a catalog dict (alias for catalog tool count on survivor data). */
export function retrieveCatalogToolCount(data: JsonRecord): number {
  return retrieveCatalogToolCountNative(data);
}

export interface RetrieveCoreOptions {
  applyDecomposedScoreFilter?: boolean;
  policyOptions?: PolicyOptions | null;
}

/** Low-level retrieve over survivor json files (same core as Go/C ``cyt_retrieve_core``). */
export function retrieveCore(
  data: JsonRecord,
  storeJsonFiles: Record<string, unknown>,
  survivorJsonFiles: Record<string, unknown>,
  options: RetrieveCoreOptions = {},
): JsonRecord[] {
  const { applyDecomposedScoreFilter = false, policyOptions = null } = options;
  return retrieveCoreNative(
    data,
    storeJsonFiles,
    survivorJsonFiles,
    applyDecomposedScoreFilter,
    policyOptions ?? undefined,
  ) as JsonRecord[];
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
    applyDecomposedScoreFilter = false,
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
