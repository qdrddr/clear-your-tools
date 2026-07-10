export type JsonRecord = Record<string, unknown>;

export function isJsonRecord(value: unknown): value is JsonRecord {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

/** Cached token metadata for one tool schema file in a catalog index. */
export interface ToolSchemaTokenFileEntry {
  file_path: string;
  token_count: number;
}

/** Cached full/decomposed tool schema token metadata from catalog index files. */
export interface ToolSchemaMetadata {
  full: ToolSchemaTokenFileEntry | { files: ToolSchemaTokenFileEntry[] } | null;
  decomposed: ToolSchemaTokenFileEntry[] | null;
}

/** One node/chunk row from skill line-content retrieval. */
export interface SkillLineContentRow {
  line_num?: number;
  node_id?: number;
  chunk_id?: number;
  content: string;
  token_count?: number;
}

/** One rerankable skill node item from buildSkillNodeCatalog. */
export interface SkillNodeCatalogItem {
  entry_dir: string;
  doc_id: string;
  node_id: number;
  file_path: string;
  content: string;
  score: string | number;
  token_count?: number;
}

/** classifyAndCountCatalog result shape. */
export interface ClassifyAndCountCatalogResult {
  optional_chunk_count: number;
  system_optional: boolean[];
  mcp_optional: boolean[];
  catalog_tool_count: number;
  tokens?: number;
  tool_count?: number;
}
