/** Document text extraction for catalog chunks (Rust-backed). */

import {
  extractDocumentTextNative,
  extractJsonCatalogDocumentNative,
  extractLevelInfoNative,
  extractMdCatalogDocumentNative,
} from "./native.js";
import type { JsonRecord } from "./types.js";

export function extractJsonCatalogDocument(item: JsonRecord): string | null {
  return extractJsonCatalogDocumentNative(item);
}

export function extractMdCatalogDocument(item: JsonRecord): string | null {
  return extractMdCatalogDocumentNative(item);
}

export function extractDocumentText(itemContent: JsonRecord): string | null {
  return extractDocumentTextNative(itemContent);
}

export function extractLevelInfo(data: JsonRecord): string[] {
  return extractLevelInfoNative(data);
}
