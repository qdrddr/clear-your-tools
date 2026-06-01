import { CatalogIndex } from "./build.js";
import { loadCatalog as loadCatalogNative, retrieveCore, type PolicyOptions } from "./native.js";
import {
  DECOMPOSED_PREFIX,
  getRootToolKey,
  JSON_EXT,
  toDecomposedKey,
  toolIdFromDecomposedRel,
} from "./paths.js";

export const DECOMPOSED_SCORE = 0.5;
export const ENUM_SCORE = 0.2;

export type JsonRecord = Record<string, unknown>;

export type { PolicyOptions };

export class DecomposedCatalog {
  private readonly jsonFiles: Record<string, JsonRecord>;

  constructor(jsonFiles: Record<string, JsonRecord> = {}) {
    this.jsonFiles = jsonFiles;
  }

  static fromCatalogIndex(index: CatalogIndex): DecomposedCatalog {
    const jsonFiles: Record<string, JsonRecord> = {};
    for (const [relPath, content] of Object.entries(index.files)) {
      if (
        relPath.startsWith(DECOMPOSED_PREFIX) &&
        relPath.endsWith(JSON_EXT)
      ) {
        jsonFiles[relPath] = JSON.parse(content) as JsonRecord;
      }
    }
    return new DecomposedCatalog(jsonFiles);
  }

  static fromCatalogDict(data: JsonRecord): DecomposedCatalog {
    const jsonFiles: Record<string, JsonRecord> = {};
    const entries = data.json;
    if (Array.isArray(entries)) {
      for (const entry of entries) {
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
          continue;
        }
        const record = entry as JsonRecord;
        const filePath = record.file_path;
        const content = record.content;
        if (typeof filePath !== "string" || !content || typeof content !== "object") {
          continue;
        }
        const key = toDecomposedKey(filePath);
        if (key !== null) {
          jsonFiles[key] = content as JsonRecord;
        }
      }
    }
    return new DecomposedCatalog(jsonFiles);
  }

  resolveKey(filePath: string): string | null {
    const candidates: string[] = [];
    const normalized = toDecomposedKey(filePath);
    if (normalized !== null) {
      candidates.push(normalized);
    }
    candidates.push(filePath);
    for (const candidate of candidates) {
      if (this.hasJson(candidate)) {
        return candidate;
      }
    }
    return null;
  }

  hasJson(key: string): boolean {
    return key in this.jsonFiles;
  }

  getJson(key: string): JsonRecord | undefined {
    return this.jsonFiles[key];
  }

  toJsonFiles(): Record<string, JsonRecord> {
    return { ...this.jsonFiles };
  }
}

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
  const { catalog, applyDecomposedScoreFilter = true, policyOptions = null } =
    options;

  let store: DecomposedCatalog;
  if (catalog instanceof DecomposedCatalog) {
    store = catalog;
  } else if (catalog instanceof CatalogIndex) {
    store = DecomposedCatalog.fromCatalogIndex(catalog);
  } else {
    throw new TypeError("catalog must be DecomposedCatalog or CatalogIndex");
  }

  const catalogDict =
    data && typeof data === "object" && !Array.isArray(data)
      ? (data as JsonRecord)
      : {};
  const survivorStore = DecomposedCatalog.fromCatalogDict(catalogDict);

  const result = retrieveCore(
    catalogDict,
    store.toJsonFiles(),
    survivorStore.toJsonFiles(),
    applyDecomposedScoreFilter,
    policyOptions ?? undefined,
  );

  return result as JsonRecord[];
}

export function buildPolicyOptionsFromToolNames(
  toolNames: Iterable<string>,
  effectivePolicy: (toolName: string) => string,
): PolicyOptions | undefined {
  const pruneOptionalTools: string[] = [];
  for (const toolName of toolNames) {
    if (effectivePolicy(toolName) === "prune_optional") {
      pruneOptionalTools.push(toolName);
    }
  }
  if (pruneOptionalTools.length === 0) {
    return undefined;
  }
  return { pruneOptionalTools };
}

export function rootToolNamesFromCatalog(store: DecomposedCatalog): string[] {
  const names = new Set<string>();
  for (const key of Object.keys(store.toJsonFiles())) {
    const rootTool = getRootToolKey(key);
    if (rootTool === null) {
      continue;
    }
    names.add(toolIdFromDecomposedRel(rootTool));
  }
  return [...names];
}
