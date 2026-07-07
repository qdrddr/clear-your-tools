/** Rust-backed disk/memory cache for skills and tool catalogs. */

import {
  configureMemoryCacheNative,
  ensureSkillsRegistryNative,
  ensureToolCatalogFromEntriesNative,
  ensureToolCatalogNative,
  toolsCatalogContentHashNative,
} from "./native.js";

export type CachePolicy = "auto" | "force_memory" | "force_disk";

export interface ToolCatalogCacheResult {
  catalog: Record<string, unknown>;
  index: { tools: unknown[]; files: Record<string, string> };
  entry_dir: string;
  content_hash: string;
  disk_backed: boolean;
  cache_status: "hit" | "miss" | "memory_fallback";
}

export interface SkillEntryRef {
  entry_dir: string;
  doc_id: string;
  content_sha256: string;
  bm25_chunk_dir: string | null;
  disk_backed: boolean;
  cache_status: "hit" | "miss" | "memory_fallback";
  source_path: string;
  nodes_dir: string | null;
  document: Record<string, unknown>;
  lazy_pending?: boolean;
}

/** Filesystem path or in-memory hook/client skill payload. */
export type SkillSourceInput =
  | string
  | {
      path: string;
      content?: string;
      content_sha256?: string;
    };

export function toolsCatalogContentHash(
  tools: unknown[],
  policyFingerprint: string,
): string {
  return toolsCatalogContentHashNative(tools, policyFingerprint);
}

export function ensureToolCatalog(
  tools: unknown[],
  policyFingerprint: string,
  toolsRoot: string,
  policy?: CachePolicy,
): ToolCatalogCacheResult {
  return ensureToolCatalogNative(
    tools,
    policyFingerprint,
    toolsRoot,
    policy,
  ) as ToolCatalogCacheResult;
}

export function ensureToolCatalogFromEntries(
  entries: unknown[],
  enums: unknown[],
  policyFingerprint: string,
  toolsRoot: string,
  policy?: CachePolicy,
): ToolCatalogCacheResult {
  return ensureToolCatalogFromEntriesNative(
    entries,
    enums,
    policyFingerprint,
    toolsRoot,
    policy,
  ) as ToolCatalogCacheResult;
}

export function ensureSkillsRegistry(
  sourcePaths: SkillSourceInput[],
  catalogRoot: string,
  pageindexConfig: Record<string, unknown> | null | undefined,
  pipeline: string,
  indexParamsHash: string,
  policy?: CachePolicy,
): SkillEntryRef[] {
  return ensureSkillsRegistryNative(
    sourcePaths,
    catalogRoot,
    pageindexConfig ?? undefined,
    pipeline,
    indexParamsHash,
    policy,
  ) as SkillEntryRef[];
}

export function configureMemoryCache(config: Record<string, unknown>): void {
  configureMemoryCacheNative(config);
}
