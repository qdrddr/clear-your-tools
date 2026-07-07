import {
  collectEnumsNative,
  configurePathConstantsNative,
  getRootToolKeyNative,
  pathBuilderMemoryOnlyNative,
  pathCatalogPrefixNative,
  pathDefaultCatalogDirNative,
  pathDecomposedPrefixNative,
  pathDecomposedRootNative,
  pathJsonExtNative,
  pathMdExtNative,
  pathWriteCatalogPruneNative,
  toDecomposedKeyNative,
  toolIdFromDecomposedRelNative,
} from "./native.js";
import type { JsonRecord } from "./types.js";

/** Push host app overrides into native PathConfig (Rust defaults when not called). */
export function configurePathConstants(opts: {
  mdExt: string;
  jsonExt: string;
  decomposedPrefix: string;
  decomposedRoot: string;
  catalogPrefix: string;
  builderMemoryOnly: boolean;
  defaultCatalogDir: string;
  writeCatalogPrune: boolean;
}): void {
  configurePathConstantsNative({
    mdExt: opts.mdExt,
    jsonExt: opts.jsonExt,
    decomposedPrefix: opts.decomposedPrefix,
    decomposedRoot: opts.decomposedRoot,
    catalogPrefix: opts.catalogPrefix,
    builderMemoryOnly: opts.builderMemoryOnly,
    defaultCatalogDir: opts.defaultCatalogDir,
    writeCatalogPrune: opts.writeCatalogPrune,
  });
}

export function mdExt(): string {
  return pathMdExtNative();
}

export function jsonExt(): string {
  return pathJsonExtNative();
}

export function decomposedPrefix(): string {
  return pathDecomposedPrefixNative();
}

export function decomposedRoot(): string {
  return pathDecomposedRootNative();
}

export function catalogPrefix(): string {
  return pathCatalogPrefixNative();
}

export function defaultCatalogDir(): string {
  return pathDefaultCatalogDirNative();
}

export function builderMemoryOnly(): boolean {
  return pathBuilderMemoryOnlyNative();
}

export function writeCatalogPrune(): boolean {
  return pathWriteCatalogPruneNative();
}

export function toDecomposedKey(filePath: string): string | null {
  return toDecomposedKeyNative(filePath);
}

export function toolIdFromDecomposedRel(relPath: string): string {
  return toolIdFromDecomposedRelNative(relPath);
}

export function getRootToolKey(filePath: string): string | null {
  return getRootToolKeyNative(filePath);
}

export function collectEnums(schema: unknown): unknown[] {
  return collectEnumsNative(schema as JsonRecord);
}
