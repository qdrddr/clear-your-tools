import {
  DECOMPOSED_PREFIX,
  JSON_EXT,
  MD_EXT,
  toDecomposedKey,
  toolIdFromDecomposedRel,
} from "./decomposed-paths.js";
import { isJsonRecord, type JsonRecord } from "./types.js";

export function parseDecomposedJsonContent(content: string): JsonRecord | null {
  const parsed: unknown = JSON.parse(content);
  return isJsonRecord(parsed) ? parsed : null;
}

export function maybeDecomposedJsonFile(
  relPath: string,
  content: string,
): JsonRecord | null {
  if (!relPath.startsWith(DECOMPOSED_PREFIX) || !relPath.endsWith(JSON_EXT)) {
    return null;
  }
  return parseDecomposedJsonContent(content);
}

function isCatalogJsonEntry(
  entry: unknown,
): entry is JsonRecord & { file_path: string; content: JsonRecord } {
  return (
    isJsonRecord(entry) &&
    typeof entry.file_path === "string" &&
    isJsonRecord(entry.content)
  );
}

export function parseCatalogJsonEntry(
  entry: unknown,
): [string, JsonRecord] | null {
  if (!isCatalogJsonEntry(entry)) {
    return null;
  }
  const key = toDecomposedKey(entry.file_path);
  return key === null ? null : [key, entry.content];
}

function enumValuesFromRecord(record: Record<string, unknown>): unknown[] {
  const here = Array.isArray(record.enum) ? record.enum : [];
  return [...here, ...Object.values(record).flatMap(enumValuesFromNode)];
}

function enumValuesFromNode(node: unknown): unknown[] {
  if (Array.isArray(node)) {
    return node.flatMap(enumValuesFromNode);
  }
  if (!node || typeof node !== "object") {
    return [];
  }
  return enumValuesFromRecord(node as Record<string, unknown>);
}

export function collectSchemaEnums(schema: unknown): unknown[] {
  return enumValuesFromNode(schema);
}

function catalogFilePath(catalogPrefix: string, relPath: string): string {
  return `${catalogPrefix}/${relPath}`;
}

function catalogEntryId(relPath: string, parsed: JsonRecord): string {
  return (
    (typeof parsed.id === "string" ? parsed.id : undefined) ??
    toolIdFromDecomposedRel(relPath)
  );
}

function buildMdCatalogEntry(
  relPath: string,
  content: string,
  catalogPrefix: string,
): JsonRecord {
  const filePath = catalogFilePath(catalogPrefix, relPath);
  const id = relPath.slice(
    relPath.lastIndexOf("/") + 1,
    relPath.length - MD_EXT.length,
  );
  return {
    id,
    file_path: filePath,
    score: 1.0,
    start_line: 1,
    end_line: 1,
    language: "markdown",
    content,
  };
}

function buildJsonCatalogEntry(
  relPath: string,
  content: string,
  catalogPrefix: string,
): JsonRecord {
  const parsed = parseDecomposedJsonContent(content);
  if (parsed === null) {
    throw new TypeError(`catalog JSON must be an object: ${relPath}`);
  }
  const filePath = catalogFilePath(catalogPrefix, relPath);
  const entryId = catalogEntryId(relPath, parsed);
  return {
    id: entryId,
    name: entryId,
    file_path: filePath,
    score: 1.0,
    start_line: 1,
    end_line: content.split("\n").length,
    language: "json",
    content: parsed,
  };
}

export function appendDecomposedCatalogEntry(
  relPath: string,
  content: string,
  catalogPrefix: string,
  mdEntries: JsonRecord[],
  jsonEntries: JsonRecord[],
): void {
  const suffix = relPath.slice(relPath.lastIndexOf(".")).toLowerCase();
  if (suffix === MD_EXT) {
    mdEntries.push(buildMdCatalogEntry(relPath, content, catalogPrefix));
    return;
  }
  if (suffix === JSON_EXT) {
    jsonEntries.push(buildJsonCatalogEntry(relPath, content, catalogPrefix));
  }
}
