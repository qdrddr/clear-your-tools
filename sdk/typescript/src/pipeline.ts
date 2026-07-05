/** Composite pipeline APIs (Rust-backed). */

import {
  buildSkillNodeCatalogNative,
  classifyAndCountCatalogNative,
  pruneCatalogBm25AndRetrieveNative,
  searchSkillsAndSelectNative,
} from "./native.js";
import { PolicyContextNative } from "./native.js";
import type { JsonRecord } from "./types.js";

type PolicyContext = InstanceType<typeof PolicyContextNative>;

export interface PruneBm25Options {
  scoreTool?: number;
  scoreToolEnum?: number;
  pruneEnums?: boolean;
  pipeline?: string[];
}

export interface SearchSkillsOptions {
  threshold?: number;
  maxTokens?: number;
  frontmatterUpperLimit?: number;
  itemKind?: string;
}

function pruneOptionsToNative(
  options?: PruneBm25Options,
): JsonRecord | undefined {
  if (!options) {
    return undefined;
  }
  const out: JsonRecord = {};
  if (options.scoreTool !== undefined) {
    out.score_tool = options.scoreTool;
  }
  if (options.scoreToolEnum !== undefined) {
    out.score_tool_enum = options.scoreToolEnum;
  }
  if (options.pruneEnums !== undefined) {
    out.prune_enums = options.pruneEnums;
  }
  if (options.pipeline !== undefined) {
    out.pipeline = options.pipeline;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function searchOptionsToNative(
  options?: SearchSkillsOptions,
): JsonRecord | undefined {
  if (!options) {
    return undefined;
  }
  const out: JsonRecord = {};
  if (options.threshold !== undefined) {
    out.threshold = options.threshold;
  }
  if (options.maxTokens !== undefined) {
    out.max_tokens = options.maxTokens;
  }
  if (options.frontmatterUpperLimit !== undefined) {
    out.frontmatter_upper_limit = options.frontmatterUpperLimit;
  }
  if (options.itemKind !== undefined) {
    out.item_kind = options.itemKind;
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

export function pruneCatalogBm25AndRetrieve(
  catalogData: JsonRecord,
  buildCatalog: JsonRecord,
  catalogIndex: JsonRecord,
  query: string,
  scoringCtx: PolicyContext,
  outputCtx: PolicyContext,
  options?: PruneBm25Options,
): JsonRecord {
  return pruneCatalogBm25AndRetrieveNative(
    catalogData,
    buildCatalog,
    catalogIndex,
    query,
    scoringCtx,
    outputCtx,
    pruneOptionsToNative(options),
  ) as JsonRecord;
}

export function classifyAndCountCatalog(
  catalogData: JsonRecord,
  tools?: JsonRecord[] | null,
): JsonRecord {
  return classifyAndCountCatalogNative(
    catalogData,
    tools ?? undefined,
  ) as JsonRecord;
}

export function searchSkillsAndSelect(
  entries: JsonRecord[],
  query: string,
  options?: SearchSkillsOptions,
): JsonRecord {
  return searchSkillsAndSelectNative(
    entries,
    query,
    searchOptionsToNative(options),
  ) as JsonRecord;
}

export function buildSkillNodeCatalog(entries: JsonRecord[]): unknown[] {
  return buildSkillNodeCatalogNative(entries) as unknown[];
}
