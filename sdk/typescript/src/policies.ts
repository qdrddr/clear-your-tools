/** Tool policy helpers (Rust-backed). */

import {
  anthropicToolIsMcpNative,
  anthropicToolIsSystemNative,
  appendDescriptionReinstateEntriesNative,
  catalogNeedsPartitionNative,
  catalogNeedsPrunedRecomposeNative,
  chunkToolIdNative,
  directRootOptionalChunksForToolNative,
  dropRecomposedToolsWithEmptyPropertiesNative,
  effectivePolicyNative,
  entriesForPolicyNative,
  ensureRootJsonForSurvivingToolsNative,
  filterRecomposeJsonEntriesNative,
  fullPassThroughNative,
  isDecomposedOptionalPropertyChunkNative,
  isDecomposedToolRootChunkNative,
  isDescriptionPolicyNative,
  isDirectRootOptionalPropertyChunkNative,
  isMcpOptionalChunkNative,
  classifyOptionalChunksBatchNative,
  isMcpRootChunkNative,
  isNonSystemChunkNative,
  isNonSystemToolIdNative,
  isSystemChunkNative,
  isSystemOptionalChunkNative,
  isSystemRootChunkNative,
  isSystemToolIdNative,
  jsonEntriesForRecomposeNative,
  mergeCatalogNative,
  mergeToolsPreservingOrderNative,
  mitigateEmptyOptionalPropertiesNative,
  mcpRequiredEnumValuesNative,
  mcpToolsPassThroughNative,
  needsDescriptionReinstateNative,
  needsPartitionNative,
  needsPrunedRecomposeNative,
  optionalLeafSurvivedRerankNative,
  partitionCatalogNative,
  policyContextFromValuesNative,
  requestPassThroughNative,
  requiredEnumValuesByToolNative,
  restoreMcpToolsNative,
  restoreSystemToolsNative,
  rootChunkPropertiesEmptyNative,
  rootToolIdFromChunkNative,
  scoringPolicyNative,
  splitAnthropicToolsNative,
  stashMcpToolsNative,
  stashSystemToolsNative,
  systemRequiredEnumValuesNative,
  systemToolsPassThroughNative,
  toolIdHadEmptyOriginalRootPropertiesNative,
  toolIdHasEmptyDecomposedRootNative,
  toolPassThroughNative,
  batchToolPassThroughNative,
  toolPoliciesNative,
  toolsForCatalogNative,
  PolicyContextNative,
} from "./native.js";
import type { JsonRecord } from "./types.js";

type PolicyContext = InstanceType<typeof PolicyContextNative>;

export type SystemToolPolicy =
  | "always_include"
  | "prune_optional"
  | "prune_all"
  | "prune_optional_descriptions"
  | "prune_all_descriptions";
export type MCPToolPolicy =
  | "always_include"
  | "prune_optional"
  | "prune_all"
  | "prune_optional_descriptions"
  | "prune_all_descriptions";
export type ToolPolicy =
  | "always_include"
  | "prune_optional"
  | "prune_all"
  | "prune_optional_descriptions"
  | "prune_all_descriptions";
/** Batch override on native `PolicyContext.toolKind` (`"system"` | `"mcp"`). */
export type ToolKind = "system" | "mcp";

export { PolicyContextNative as PolicyContext };

/** Set batch tool-kind override on a native policy context (mirrors Python apply_executor_tool_kind). */
export function applyToolKind(
  ctx: PolicyContext,
  kind: ToolKind,
): PolicyContext {
  ctx.toolKind = kind;
  return ctx;
}

/**
 * Map description policies to base scoring policies for partition/pipeline.
 * Copies toolKind (mirrors Python scoring_policy_context).
 */
export function scoringPolicyContext(ctx: PolicyContext): PolicyContext {
  const scoring = new PolicyContextNative(
    scoringPolicy(ctx.systemPolicy),
    scoringPolicy(ctx.mcpPolicy),
  );
  scoring.perTool = Object.fromEntries(
    Object.entries(ctx.perTool).map(([toolId, policy]) => [
      toolId,
      scoringPolicy(policy),
    ]),
  );
  scoring.toolKind = ctx.toolKind;
  return scoring;
}

export function toolPolicies(): string[] {
  return toolPoliciesNative();
}

export function policyContextFromValues(config: JsonRecord): PolicyContext {
  return policyContextFromValuesNative(config);
}

export function effectivePolicy(
  toolId: string,
  ctx: PolicyContext,
): ToolPolicy {
  return effectivePolicyNative(ctx, toolId) as ToolPolicy;
}

export function toolPassThrough(toolId: string, ctx: PolicyContext): boolean {
  return toolPassThroughNative(ctx, toolId);
}

export function batchToolPassThrough(
  toolIds: string[],
  ctx: PolicyContext,
): boolean[] {
  return batchToolPassThroughNative(ctx, toolIds);
}

export function rootToolIdFromChunk(item: JsonRecord): string {
  return rootToolIdFromChunkNative(item);
}

export function requestPassThrough(
  tools: JsonRecord[],
  ctx: PolicyContext,
): boolean {
  return requestPassThroughNative(ctx, tools);
}

export function isNonSystemToolId(toolId: string): boolean {
  return isNonSystemToolIdNative(toolId);
}

export function isSystemToolId(toolId: string): boolean {
  return isSystemToolIdNative(toolId);
}

export function chunkToolId(item: JsonRecord): string {
  return chunkToolIdNative(item);
}

export function isNonSystemChunk(item: JsonRecord): boolean {
  return isNonSystemChunkNative(item);
}

export function isSystemChunk(item: JsonRecord): boolean {
  return isSystemChunkNative(item);
}

export function isDecomposedToolRootChunk(item: JsonRecord): boolean {
  return isDecomposedToolRootChunkNative(item);
}

export function isDecomposedOptionalPropertyChunk(item: JsonRecord): boolean {
  return isDecomposedOptionalPropertyChunkNative(item);
}

export function isSystemRootChunk(item: JsonRecord): boolean {
  return isSystemRootChunkNative(item);
}

export function isMcpRootChunk(item: JsonRecord): boolean {
  return isMcpRootChunkNative(item);
}

export function isSystemOptionalChunk(item: JsonRecord): boolean {
  return isSystemOptionalChunkNative(item);
}

export function isMcpOptionalChunk(item: JsonRecord): boolean {
  return isMcpOptionalChunkNative(item);
}

export function classifyOptionalChunksBatch(items: JsonRecord[]): {
  system: boolean[];
  mcp: boolean[];
} {
  return classifyOptionalChunksBatchNative(items);
}

export function needsPartition(ctx: PolicyContext): boolean {
  return needsPartitionNative(ctx);
}

export function needsPrunedRecompose(ctx: PolicyContext): boolean {
  return needsPrunedRecomposeNative(ctx);
}

export function systemToolsPassThrough(ctx: PolicyContext): boolean {
  return systemToolsPassThroughNative(ctx);
}

export function mcpToolsPassThrough(ctx: PolicyContext): boolean {
  return mcpToolsPassThroughNative(ctx);
}

export function fullPassThrough(ctx: PolicyContext): boolean {
  return fullPassThroughNative(ctx);
}

export function catalogNeedsPartition(
  data: JsonRecord,
  ctx: PolicyContext,
): boolean {
  return catalogNeedsPartitionNative(data, ctx);
}

export function catalogNeedsPrunedRecompose(
  data: JsonRecord,
  ctx: PolicyContext,
): boolean {
  return catalogNeedsPrunedRecomposeNative(data, ctx);
}

export function partitionCatalog(
  data: JsonRecord,
  ctx: PolicyContext,
): [JsonRecord, JsonRecord] {
  return partitionCatalogNative(data, ctx) as [JsonRecord, JsonRecord];
}

export function mergeCatalog(
  processed: JsonRecord,
  pinned: JsonRecord,
): JsonRecord {
  return mergeCatalogNative(processed, pinned) as JsonRecord;
}

export function stashSystemTools(tools: JsonRecord[]): JsonRecord[] {
  return stashSystemToolsNative(tools) as JsonRecord[];
}

export function restoreSystemTools(stash: JsonRecord[]): JsonRecord[] {
  return restoreSystemToolsNative(stash) as JsonRecord[];
}

export function stashMcpTools(tools: JsonRecord[]): JsonRecord[] {
  return stashMcpToolsNative(tools) as JsonRecord[];
}

export function restoreMcpTools(stash: JsonRecord[]): JsonRecord[] {
  return restoreMcpToolsNative(stash) as JsonRecord[];
}

export function mergeToolsPreservingOrder(
  original: JsonRecord[],
  prunedByName: Record<string, JsonRecord>,
  stashedByName: Record<string, JsonRecord>,
): JsonRecord[] {
  return mergeToolsPreservingOrderNative(
    original,
    prunedByName,
    stashedByName,
  ) as JsonRecord[];
}

export function anthropicToolIsSystem(tool: JsonRecord): boolean {
  return anthropicToolIsSystemNative(tool);
}

export function anthropicToolIsMcp(tool: JsonRecord): boolean {
  return anthropicToolIsMcpNative(tool);
}

export function splitAnthropicTools(
  tools: JsonRecord[],
): [JsonRecord[], JsonRecord[]] {
  const result = splitAnthropicToolsNative(tools);
  return [result.nonSystem as JsonRecord[], result.system as JsonRecord[]];
}

export function entriesForPolicy(
  allEntries: JsonRecord[],
  ctx: PolicyContext,
): JsonRecord[] {
  return entriesForPolicyNative(ctx, allEntries) as JsonRecord[];
}

export function toolsForCatalog(
  tools: JsonRecord[],
  ctx: PolicyContext,
): JsonRecord[] {
  return toolsForCatalogNative(ctx, tools) as JsonRecord[];
}

export function systemRequiredEnumValues(data: JsonRecord): string[] {
  return systemRequiredEnumValuesNative(data);
}

export function mcpRequiredEnumValues(data: JsonRecord): string[] {
  return mcpRequiredEnumValuesNative(data);
}

export function requiredEnumValuesByTool(
  data: JsonRecord,
): Record<string, string[]> {
  return requiredEnumValuesByToolNative(data);
}

export function optionalLeafSurvivedRerank(
  item: JsonRecord,
  ctx: PolicyContext,
  rerankScore?: number | null,
  llmSelectedPaths?: Iterable<string> | null,
): boolean {
  return optionalLeafSurvivedRerankNative(
    item,
    ctx,
    rerankScore ?? undefined,
    llmSelectedPaths === undefined || llmSelectedPaths === null
      ? undefined
      : [...llmSelectedPaths],
  );
}

export function filterRecomposeJsonEntries(
  jsonList: JsonRecord[],
  ctx: PolicyContext,
  rerankScore?: number | null,
  llmSelectedPaths?: Iterable<string> | null,
): JsonRecord[] {
  return filterRecomposeJsonEntriesNative(
    jsonList,
    ctx,
    rerankScore ?? undefined,
    llmSelectedPaths === undefined || llmSelectedPaths === null
      ? undefined
      : [...llmSelectedPaths],
  ) as JsonRecord[];
}

export function mitigateEmptyOptionalProperties(
  entries: JsonRecord[],
  catalogIndex: JsonRecord,
  ctx: PolicyContext,
  postRerankScored: JsonRecord | null | undefined,
  pipeline: string[],
): JsonRecord[] {
  return mitigateEmptyOptionalPropertiesNative(
    entries,
    catalogIndex,
    ctx,
    postRerankScored ?? undefined,
    pipeline,
  ) as JsonRecord[];
}

export function ensureRootJsonForSurvivingTools(
  entries: JsonRecord[],
  buildCatalog: JsonRecord,
): JsonRecord[] {
  return ensureRootJsonForSurvivingToolsNative(
    entries,
    buildCatalog,
  ) as JsonRecord[];
}

export function jsonEntriesForRecompose(
  data: JsonRecord,
  pinned: JsonRecord | null | undefined,
  buildCatalog: JsonRecord,
  postRerankScored: JsonRecord | null | undefined,
  ctx: PolicyContext,
  catalogIndex: JsonRecord,
  pipeline: string[],
): JsonRecord[] {
  return jsonEntriesForRecomposeNative(
    data,
    pinned ?? undefined,
    buildCatalog,
    postRerankScored ?? undefined,
    ctx,
    catalogIndex,
    pipeline,
  ) as JsonRecord[];
}

export function appendDescriptionReinstateEntries(
  entries: JsonRecord[],
  buildCatalog: JsonRecord,
  catalogIndex: JsonRecord,
  ctx: PolicyContext,
): JsonRecord[] {
  return appendDescriptionReinstateEntriesNative(
    entries,
    buildCatalog,
    catalogIndex,
    ctx,
  ) as JsonRecord[];
}

export function needsDescriptionReinstate(ctx: PolicyContext): boolean {
  return needsDescriptionReinstateNative(ctx);
}

export function isDescriptionPolicy(policy: string): boolean {
  return isDescriptionPolicyNative(policy);
}

export function scoringPolicy(policy: string): ToolPolicy {
  return scoringPolicyNative(policy) as ToolPolicy;
}

export function directRootOptionalChunksForTool(
  items: JsonRecord[],
  toolId: string,
): JsonRecord[] {
  return directRootOptionalChunksForToolNative(items, toolId) as JsonRecord[];
}

export function rootChunkPropertiesEmpty(item: JsonRecord): boolean {
  return rootChunkPropertiesEmptyNative(item);
}

export function toolIdHasEmptyDecomposedRoot(
  catalogIndex: JsonRecord,
  toolId: string,
): boolean {
  return toolIdHasEmptyDecomposedRootNative(catalogIndex, toolId);
}

export function toolIdHadEmptyOriginalRootProperties(
  catalogIndex: JsonRecord,
  toolId: string,
): boolean {
  return toolIdHadEmptyOriginalRootPropertiesNative(catalogIndex, toolId);
}

export function isDirectRootOptionalPropertyChunk(item: JsonRecord): boolean {
  return isDirectRootOptionalPropertyChunkNative(item);
}

export function dropRecomposedToolsWithEmptyProperties(
  tools: JsonRecord[],
  catalogIndex: JsonRecord,
  ctx: PolicyContext,
): JsonRecord[] {
  return dropRecomposedToolsWithEmptyPropertiesNative(
    tools,
    catalogIndex,
    ctx,
  ) as JsonRecord[];
}
