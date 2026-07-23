package cytindexer

// ToolPolicies returns the list of supported tool policy names as JSON.
func ToolPolicies() (string, error) {
	return cgoToolPolicies()
}

// PolicyContextFromValues builds a policy context JSON from config values.
func PolicyContextFromValues(configJSON string) (string, error) {
	return cgoPolicyContextFromValues(configJSON)
}

// EffectivePolicy returns the effective policy for a tool id.
func EffectivePolicy(ctxJSON, toolID string) (string, error) {
	return cgoEffectivePolicy(ctxJSON, toolID)
}

// BatchToolPassThrough returns pass-through flags for tool ids (JSON bool array).
func BatchToolPassThrough(ctxJSON, toolIDsJSON string) (string, error) {
	return cgoBatchToolPassThrough(ctxJSON, toolIDsJSON)
}

// PartitionCatalog partitions catalog data by policy context.
func PartitionCatalog(dataJSON, ctxJSON string) (string, error) {
	return cgoPartitionCatalog(dataJSON, ctxJSON)
}

// MergeCatalog merges processed and pinned catalog partitions.
func MergeCatalog(processedJSON, pinnedJSON string) (string, error) {
	return cgoMergeCatalog(processedJSON, pinnedJSON)
}

// CatalogNeedsPartition reports whether catalog data needs partitioning.
func CatalogNeedsPartition(dataJSON, ctxJSON string) (bool, error) {
	return cgoCatalogNeedsPartition(dataJSON, ctxJSON)
}

// CatalogNeedsPrunedRecompose reports whether catalog needs pruned recompose.
func CatalogNeedsPrunedRecompose(dataJSON, ctxJSON string) (bool, error) {
	return cgoCatalogNeedsPrunedRecompose(dataJSON, ctxJSON)
}

// RequestPassThrough reports whether tools should pass through unchanged.
func RequestPassThrough(ctxJSON, toolsJSON string) (bool, error) {
	return cgoRequestPassThrough(ctxJSON, toolsJSON)
}

// FilterRecomposeJSONEntries filters JSON entries for recompose.
func FilterRecomposeJSONEntries(jsonListJSON, ctxJSON string, rerankScore float64, useDefaultRerankScore bool, llmSelectedPathsJSON string) (string, error) {
	return cgoFilterRecomposeJSONEntries(jsonListJSON, ctxJSON, rerankScore, useDefaultRerankScore, llmSelectedPathsJSON)
}

// MitigateEmptyOptionalProperties mitigates empty optional properties in entries.
func MitigateEmptyOptionalProperties(entriesJSON, catalogIndexJSON, ctxJSON, postRerankScoredJSON, pipelineJSON string) (string, error) {
	return cgoMitigateEmptyOptionalProperties(entriesJSON, catalogIndexJSON, ctxJSON, postRerankScoredJSON, pipelineJSON)
}

// EnsureRootJSONForSurvivingTools injects root json chunks when any json chunk survives.
func EnsureRootJSONForSurvivingTools(entriesJSON, buildCatalogJSON string) (string, error) {
	return cgoEnsureRootJSONForSurvivingTools(entriesJSON, buildCatalogJSON)
}

// JSONEntriesForRecompose returns filtered json entries for catalog recompose.
func JSONEntriesForRecompose(dataJSON, pinnedJSON, buildCatalogJSON, postRerankScoredJSON, ctxJSON, catalogIndexJSON, pipelineJSON string) (string, error) {
	return cgoJSONEntriesForRecompose(dataJSON, pinnedJSON, buildCatalogJSON, postRerankScoredJSON, ctxJSON, catalogIndexJSON, pipelineJSON)
}

// AppendDescriptionReinstateEntries appends description reinstate entries.
func AppendDescriptionReinstateEntries(entriesJSON, buildCatalogJSON, catalogIndexJSON, ctxJSON string) (string, error) {
	return cgoAppendDescriptionReinstateEntries(entriesJSON, buildCatalogJSON, catalogIndexJSON, ctxJSON)
}

// IsDescriptionPolicy reports whether a policy name is a description variant.
func IsDescriptionPolicy(policy string) (bool, error) {
	return cgoIsDescriptionPolicy(policy)
}

// ScoringPolicy maps a policy name to its scoring base policy.
func ScoringPolicy(policy string) (string, error) {
	return cgoScoringPolicy(policy)
}

// DropRecomposedToolsWithEmptyProperties drops recomposed tools with empty properties.
func DropRecomposedToolsWithEmptyProperties(toolsJSON, catalogIndexJSON, ctxJSON string) (string, error) {
	return cgoDropRecomposedToolsWithEmptyProperties(toolsJSON, catalogIndexJSON, ctxJSON)
}

// RootToolIDFromChunk returns the root tool id for a catalog chunk item.
func RootToolIDFromChunk(itemJSON string) (string, error) {
	return cgoRootToolIDFromChunk(itemJSON)
}

// ChunkToolID returns the chunk tool id for a catalog item.
func ChunkToolID(itemJSON string) (string, error) {
	return cgoChunkToolID(itemJSON)
}

// IsNonSystemToolID reports whether a tool id is non-system.
func IsNonSystemToolID(toolID string) (bool, error) {
	return cgoIsNonSystemToolID(toolID)
}

// IsSystemToolID reports whether a tool id is a system tool.
func IsSystemToolID(toolID string) (bool, error) {
	return cgoIsSystemToolID(toolID)
}

// MergeToolsPreservingOrder merges tool lists preserving original order.
func MergeToolsPreservingOrder(originalJSON, prunedByNameJSON, stashedByNameJSON string) (string, error) {
	return cgoMergeToolsPreservingOrder(originalJSON, prunedByNameJSON, stashedByNameJSON)
}

// SplitAnthropicTools splits Anthropic tools into system and MCP groups.
func SplitAnthropicTools(toolsJSON string) (string, error) {
	return cgoSplitAnthropicTools(toolsJSON)
}

// EntriesForPolicy filters catalog entries for a policy context.
func EntriesForPolicy(ctxJSON, allEntriesJSON string) (string, error) {
	return cgoEntriesForPolicy(ctxJSON, allEntriesJSON)
}

// ToolsForCatalog filters tools for a catalog under a policy context.
func ToolsForCatalog(ctxJSON, toolsJSON string) (string, error) {
	return cgoToolsForCatalog(ctxJSON, toolsJSON)
}

// SystemRequiredEnumValues returns system required enum values from catalog data.
func SystemRequiredEnumValues(dataJSON string) (string, error) {
	return cgoSystemRequiredEnumValues(dataJSON)
}

// McpRequiredEnumValues returns MCP required enum values from catalog data.
func McpRequiredEnumValues(dataJSON string) (string, error) {
	return cgoMcpRequiredEnumValues(dataJSON)
}

// RequiredEnumValuesByTool returns required enum values grouped by tool.
func RequiredEnumValuesByTool(dataJSON string) (string, error) {
	return cgoRequiredEnumValuesByTool(dataJSON)
}

// OptionalLeafSurvivedRerank reports whether an optional leaf survived rerank.
func OptionalLeafSurvivedRerank(itemJSON, ctxJSON string, rerankScore float64, useDefaultRerankScore bool, llmSelectedPathsJSON string) (bool, error) {
	return cgoOptionalLeafSurvivedRerank(itemJSON, ctxJSON, rerankScore, useDefaultRerankScore, llmSelectedPathsJSON)
}

// AnthropicToolIsSystem reports whether an Anthropic tool JSON is a system tool.
func AnthropicToolIsSystem(toolJSON string) (bool, error) {
	return cgoAnthropicToolIsSystem(toolJSON)
}

// AnthropicToolIsMcp reports whether an Anthropic tool JSON is an MCP tool.
func AnthropicToolIsMcp(toolJSON string) (bool, error) {
	return cgoAnthropicToolIsMcp(toolJSON)
}

// DirectRootOptionalChunksForTool returns direct root optional chunks for a tool.
func DirectRootOptionalChunksForTool(itemsJSON, toolID string) (string, error) {
	return cgoDirectRootOptionalChunksForTool(itemsJSON, toolID)
}

// ToolIDHasEmptyDecomposedRoot reports whether a tool id has an empty decomposed root.
func ToolIDHasEmptyDecomposedRoot(catalogIndexJSON, toolID string) (bool, error) {
	return cgoToolIDHasEmptyDecomposedRoot(catalogIndexJSON, toolID)
}

// ToolIDHadEmptyOriginalRootProperties reports whether a tool had empty original root properties.
func ToolIDHadEmptyOriginalRootProperties(catalogIndexJSON, toolID string) (bool, error) {
	return cgoToolIDHadEmptyOriginalRootProperties(catalogIndexJSON, toolID)
}
