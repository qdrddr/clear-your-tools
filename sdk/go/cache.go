package cytindexer

// ToolsCatalogContentHash returns the content hash for a tool catalog cache key.
func ToolsCatalogContentHash(toolsJSON, policyFingerprint string) (string, error) {
	return cgoToolsCatalogContentHash(toolsJSON, policyFingerprint)
}

// EnsureToolCatalog ensures a decomposed tool catalog on disk or in memory.
func EnsureToolCatalog(toolsJSON, policyFingerprint, toolsRoot, policy string) (string, error) {
	return cgoEnsureToolCatalog(toolsJSON, policyFingerprint, toolsRoot, policy)
}

// EnsureToolCatalogFromEntries ensures a decomposed catalog from prepared entries/enums JSON arrays.
func EnsureToolCatalogFromEntries(entriesJSON, enumsJSON, policyFingerprint, toolsRoot, policy string) (string, error) {
	return cgoEnsureToolCatalogFromEntries(entriesJSON, enumsJSON, policyFingerprint, toolsRoot, policy)
}

// EnsureSkillsRegistry ensures page index (+ BM25 chunks when pipeline is bm25) for skill sources.
// sourcePathsJSON is a JSON array of filesystem paths (strings) or inline source objects
// with path, optional content, and optional content_sha256 (hook/client skills).
func EnsureSkillsRegistry(sourcePathsJSON, catalogRoot, pageindexConfigJSON, pipeline, indexParamsHash, policy string) (string, error) {
	return cgoEnsureSkillsRegistry(sourcePathsJSON, catalogRoot, pageindexConfigJSON, pipeline, indexParamsHash, policy)
}

// ConfigureMemoryCache applies in-memory cache tuning (lazy registry, LRU sizes, async disk writes).
func ConfigureMemoryCache(configJSON string) error {
	return cgoConfigureMemoryCache(configJSON)
}
