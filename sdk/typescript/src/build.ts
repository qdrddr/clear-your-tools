import {
  anthropicToolToCatalogEntryNative,
  anthropicToolsToCatalogEntriesNative,
  buildCatalogFromToolsNative,
  buildCatalogIndexNative,
  catalogIndexToCatalogDictNative,
  catalogIndexToolSchemaMetadataNative,
  catalogToolCountNative,
  prepareToolEntryNative,
  truncateDescriptionNative,
} from "./native.js";
import { collectEnums } from "./paths.js";
import type { ToolSchemaMetadata } from "./types.js";
import type { JsonRecord } from "./types.js";

export { collectEnums };

export function catalogToolCount(data: JsonRecord): number {
  return catalogToolCountNative(data);
}

export class CatalogIndex {
  constructor(
    public readonly tools: JsonRecord[],
    public readonly files: Record<string, string> = {},
  ) {}

  toCatalogDict(catalogPrefix?: string): {
    md: JsonRecord[];
    json: JsonRecord[];
    tools: JsonRecord[];
  } {
    return catalogIndexToCatalogDictNative(
      { tools: this.tools, files: this.files },
      catalogPrefix,
    ) as {
      md: JsonRecord[];
      json: JsonRecord[];
      tools: JsonRecord[];
    };
  }

  toolSchemaMetadata(): ToolSchemaMetadata {
    return catalogIndexToolSchemaMetadataNative({
      tools: this.tools,
      files: this.files,
    }) as ToolSchemaMetadata;
  }
}

function catalogIndexFromRaw(raw: {
  tools: JsonRecord[];
  files: Record<string, string>;
}): CatalogIndex {
  return new CatalogIndex([...raw.tools], { ...raw.files });
}

export function buildCatalogIndex(
  tools: JsonRecord[],
  allEnums: unknown[],
): CatalogIndex {
  const raw = buildCatalogIndexNative(tools, allEnums);
  return catalogIndexFromRaw(raw);
}

export function buildCatalogFromTools(tools: JsonRecord[]): CatalogIndex {
  const raw = buildCatalogFromToolsNative(tools);
  return catalogIndexFromRaw(raw);
}

export function prepareToolEntry(
  serverName: string,
  name: string,
  description: string,
  inputSchema: JsonRecord,
): JsonRecord {
  return prepareToolEntryNative(
    serverName,
    name,
    description,
    inputSchema,
  ) as JsonRecord;
}

export function anthropicToolToCatalogEntry(
  tool: JsonRecord,
): JsonRecord | null {
  return anthropicToolToCatalogEntryNative(tool) as JsonRecord | null;
}

export function anthropicToolsToCatalogEntries(tools: JsonRecord[]): {
  entries: JsonRecord[];
  enums: unknown[];
} {
  return anthropicToolsToCatalogEntriesNative(tools) as {
    entries: JsonRecord[];
    enums: unknown[];
  };
}

export function truncateDescription(
  description: string,
  maxTokens = 60,
): string {
  return truncateDescriptionNative(description, maxTokens);
}

export function catalogIndexToolSchemaMetadata(index: {
  tools: JsonRecord[];
  files: Record<string, string>;
}): ToolSchemaMetadata {
  return catalogIndexToolSchemaMetadataNative(index) as ToolSchemaMetadata;
}
