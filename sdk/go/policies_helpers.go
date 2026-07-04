package cytindexer

// ToolPassThrough reports whether a single tool should pass through unchanged.
func ToolPassThrough(ctxJSON, toolID string) (bool, error) {
	return cgoToolPassThrough(ctxJSON, toolID)
}

// FullPassThrough reports whether all tools pass through unchanged for a context.
func FullPassThrough(ctxJSON string) (bool, error) {
	return cgoFullPassThrough(ctxJSON)
}

// NeedsPartition reports whether a policy context requires catalog partitioning.
func NeedsPartition(ctxJSON string) (bool, error) {
	return cgoNeedsPartition(ctxJSON)
}

// NeedsPrunedRecompose reports whether a policy context requires pruned recompose.
func NeedsPrunedRecompose(ctxJSON string) (bool, error) {
	return cgoNeedsPrunedRecompose(ctxJSON)
}

// SystemToolsPassThrough reports whether system tools pass through for a context.
func SystemToolsPassThrough(ctxJSON string) (bool, error) {
	return cgoSystemToolsPassThrough(ctxJSON)
}

// McpToolsPassThrough reports whether MCP tools pass through for a context.
func McpToolsPassThrough(ctxJSON string) (bool, error) {
	return cgoMcpToolsPassThrough(ctxJSON)
}

// NeedsDescriptionReinstate reports whether description reinstate is required.
func NeedsDescriptionReinstate(ctxJSON string) (bool, error) {
	return cgoNeedsDescriptionReinstate(ctxJSON)
}

// IsSystemChunk reports whether a catalog item is a system chunk.
func IsSystemChunk(itemJSON string) (bool, error) {
	return cgoIsSystemChunk(itemJSON)
}

// IsNonSystemChunk reports whether a catalog item is a non-system chunk.
func IsNonSystemChunk(itemJSON string) (bool, error) {
	return cgoIsNonSystemChunk(itemJSON)
}

// IsDecomposedToolRootChunk reports whether an item is a decomposed tool root chunk.
func IsDecomposedToolRootChunk(itemJSON string) (bool, error) {
	return cgoIsDecomposedToolRootChunk(itemJSON)
}

// IsDecomposedOptionalPropertyChunk reports whether an item is a decomposed optional property chunk.
func IsDecomposedOptionalPropertyChunk(itemJSON string) (bool, error) {
	return cgoIsDecomposedOptionalPropertyChunk(itemJSON)
}

// IsSystemRootChunk reports whether an item is a system root chunk.
func IsSystemRootChunk(itemJSON string) (bool, error) {
	return cgoIsSystemRootChunk(itemJSON)
}

// IsMcpRootChunk reports whether an item is an MCP root chunk.
func IsMcpRootChunk(itemJSON string) (bool, error) {
	return cgoIsMcpRootChunk(itemJSON)
}

// IsSystemOptionalChunk reports whether an item is a system optional chunk.
func IsSystemOptionalChunk(itemJSON string) (bool, error) {
	return cgoIsSystemOptionalChunk(itemJSON)
}

// IsMcpOptionalChunk reports whether an item is an MCP optional chunk.
func IsMcpOptionalChunk(itemJSON string) (bool, error) {
	return cgoIsMcpOptionalChunk(itemJSON)
}

// IsDirectRootOptionalPropertyChunk reports whether an item is a direct root optional property chunk.
func IsDirectRootOptionalPropertyChunk(itemJSON string) (bool, error) {
	return cgoIsDirectRootOptionalPropertyChunk(itemJSON)
}

// RootChunkPropertiesEmpty reports whether a root chunk has empty properties.
func RootChunkPropertiesEmpty(itemJSON string) (bool, error) {
	return cgoRootChunkPropertiesEmpty(itemJSON)
}

// StashSystemTools stashes system tools from a JSON tool array.
func StashSystemTools(toolsJSON string) (string, error) {
	return cgoStashSystemTools(toolsJSON)
}

// RestoreSystemTools restores stashed system tools from a JSON stash array.
func RestoreSystemTools(stashJSON string) (string, error) {
	return cgoRestoreSystemTools(stashJSON)
}

// StashMcpTools stashes MCP tools from a JSON tool array.
func StashMcpTools(toolsJSON string) (string, error) {
	return cgoStashMcpTools(toolsJSON)
}

// RestoreMcpTools restores stashed MCP tools from a JSON stash array.
func RestoreMcpTools(stashJSON string) (string, error) {
	return cgoRestoreMcpTools(stashJSON)
}
