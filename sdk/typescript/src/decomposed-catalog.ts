import { CatalogIndex } from "./build.js";
import {
  maybeDecomposedJsonFile,
  parseCatalogJsonEntry,
} from "./catalog-json.js";
import { toDecomposedKey } from "./decomposed-paths.js";
import type { JsonRecord } from "./types.js";

export class DecomposedCatalog {
  private readonly jsonFiles: Record<string, JsonRecord>;

  constructor(jsonFiles: Record<string, JsonRecord> = {}) {
    this.jsonFiles = jsonFiles;
  }

  static fromCatalogIndex(index: CatalogIndex): DecomposedCatalog {
    const jsonFiles: Record<string, JsonRecord> = {};
    for (const [relPath, content] of Object.entries(index.files)) {
      const parsed = maybeDecomposedJsonFile(relPath, content);
      if (parsed !== null) {
        jsonFiles[relPath] = parsed;
      }
    }
    return new DecomposedCatalog(jsonFiles);
  }

  static fromCatalogDict(data: JsonRecord): DecomposedCatalog {
    const jsonFiles: Record<string, JsonRecord> = {};
    const entries = data.json;
    if (Array.isArray(entries)) {
      for (const entry of entries) {
        const parsed = parseCatalogJsonEntry(entry);
        if (parsed !== null) {
          jsonFiles[parsed[0]] = parsed[1];
        }
      }
    }
    return new DecomposedCatalog(jsonFiles);
  }

  resolveKey(filePath: string): string | null {
    const normalized = toDecomposedKey(filePath);
    const candidates =
      normalized === null ? [filePath] : [normalized, filePath];
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

export function resolveDecomposedCatalog(
  catalog: DecomposedCatalog | CatalogIndex,
): DecomposedCatalog {
  if (catalog instanceof DecomposedCatalog) {
    return catalog;
  }
  if (catalog instanceof CatalogIndex) {
    return DecomposedCatalog.fromCatalogIndex(catalog);
  }
  throw new TypeError("catalog must be DecomposedCatalog or CatalogIndex");
}
