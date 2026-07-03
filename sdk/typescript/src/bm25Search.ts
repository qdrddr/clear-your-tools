/** Tantivy BM25 catalog search via the Rust core. */

import {
  bm25CatalogFingerprintNative,
  bm25FrontmatterGateNative,
  bm25ScoreCatalogNative,
  bm25SearchSkillChunksNative,
  batchReconstructSkillMatchesNative,
  configureBm25DefaultsNative,
  expSimilarityNative,
  greedySelectSkillItemsNative,
} from "./native.js";
import type { JsonRecord } from "./types.js";

export interface Bm25ScoreCatalogOptions {
  pruneJsonThreshold?: number | null;
  pruneMdThreshold?: number | null;
  pruneEnums?: boolean;
}

export interface Bm25FrontmatterGateResult {
  excluded: Array<{ entry_dir: string; doc_id: string }>;
  trace: JsonRecord;
}

export interface Bm25SearchSkillChunksResult {
  matches: JsonRecord[];
  trace: JsonRecord;
}

export function configureBm25Defaults(options?: {
  indexDir?: string;
  stemLanguage?: string;
  stopwords?: string;
  useStopwords?: boolean;
  k1?: number;
  b?: number;
  mmap?: boolean;
}): void {
  configureBm25DefaultsNative(
    options?.indexDir,
    options?.stemLanguage,
    options?.stopwords,
    options?.useStopwords,
    options?.k1,
    options?.b,
    options?.mmap,
  );
}

export function bm25CatalogFingerprint(data: JsonRecord): string {
  return bm25CatalogFingerprintNative(data);
}

export function bm25ScoreCatalog(
  data: JsonRecord,
  query: string,
  options: Bm25ScoreCatalogOptions = {},
): JsonRecord {
  return bm25ScoreCatalogNative(
    data,
    query,
    options.pruneJsonThreshold ?? undefined,
    options.pruneMdThreshold ?? undefined,
    options.pruneEnums,
  ) as JsonRecord;
}

export function bm25FrontmatterGate(
  entries: JsonRecord[],
  query: string,
  upperLimit = 0.4,
): Bm25FrontmatterGateResult {
  return bm25FrontmatterGateNative(
    entries,
    query,
    upperLimit,
  ) as Bm25FrontmatterGateResult;
}

export function bm25SearchSkillChunks(
  entries: JsonRecord[],
  query: string,
  threshold = 0.5,
  excluded?: Array<{ entry_dir: string; doc_id: string }>,
): Bm25SearchSkillChunksResult {
  return bm25SearchSkillChunksNative(
    entries,
    query,
    threshold,
    excluded,
  ) as Bm25SearchSkillChunksResult;
}

export function expSimilarity(raw: number): number {
  return expSimilarityNative(raw);
}

export function batchReconstructSkillMatches(
  groups: JsonRecord[],
): JsonRecord[] {
  return batchReconstructSkillMatchesNative(groups) as JsonRecord[];
}

export function greedySelectSkillItems(
  survivors: JsonRecord[],
  itemKind = "node",
  maxTokens = 0,
): JsonRecord {
  return greedySelectSkillItemsNative(
    survivors,
    itemKind,
    maxTokens,
  ) as JsonRecord;
}
