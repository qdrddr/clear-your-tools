import {
  collectEnumsNative,
  configurePathConstantsNative,
  getRootToolKeyNative,
  pathDecomposedPrefixNative,
  pathJsonExtNative,
  pathMdExtNative,
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
  configurePathConstantsNative(
    opts.mdExt,
    opts.jsonExt,
    opts.decomposedPrefix,
    opts.decomposedRoot,
    opts.catalogPrefix,
    opts.builderMemoryOnly,
    opts.defaultCatalogDir,
    opts.writeCatalogPrune,
  );
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
