package cytindexer

// CollectEnums extracts enum values from a JSON schema.
func CollectEnums(schemaJSON string) (string, error) {
	return cgoCollectEnums(schemaJSON)
}

// ToDecomposedKey converts a file path to a decomposed catalog key.
func ToDecomposedKey(filePath string) (string, error) {
	return cgoToDecomposedKey(filePath)
}

// ToolIDFromDecomposedRel derives a tool id from a decomposed relative path.
func ToolIDFromDecomposedRel(relPath string) (string, error) {
	return cgoToolIDFromDecomposedRel(relPath)
}

// GetRootToolKey returns the root tool key for a decomposed file path.
func GetRootToolKey(filePath string) (string, error) {
	return cgoGetRootToolKey(filePath)
}

// ConfigurePathConstants overrides native path configuration constants.
func ConfigurePathConstants(mdExt, jsonExt, decomposedPrefix, decomposedRoot, catalogPrefix, defaultCatalogDir string, builderMemoryOnly, writeCatalogPrune bool) error {
	return cgoConfigurePathConstants(mdExt, jsonExt, decomposedPrefix, decomposedRoot, catalogPrefix, defaultCatalogDir, builderMemoryOnly, writeCatalogPrune)
}

// PathMdExt returns the configured markdown file extension.
func PathMdExt() (string, error) {
	return cgoPathMdExt()
}

// PathJsonExt returns the configured JSON file extension.
func PathJsonExt() (string, error) {
	return cgoPathJsonExt()
}

// PathDecomposedPrefix returns the decomposed catalog path prefix.
func PathDecomposedPrefix() (string, error) {
	return cgoPathDecomposedPrefix()
}

// PathDecomposedRoot returns the decomposed catalog root directory.
func PathDecomposedRoot() (string, error) {
	return cgoPathDecomposedRoot()
}

// PathCatalogPrefix returns the catalog path prefix.
func PathCatalogPrefix() (string, error) {
	return cgoPathCatalogPrefix()
}

// PathDefaultCatalogDir returns the default catalog directory.
func PathDefaultCatalogDir() (string, error) {
	return cgoPathDefaultCatalogDir()
}

// PathBuilderMemoryOnly reports whether the catalog builder defaults to memory-only mode.
func PathBuilderMemoryOnly() (bool, error) {
	return cgoPathBuilderMemoryOnly()
}

// PathWriteCatalogPrune reports whether catalog writes include prune metadata.
func PathWriteCatalogPrune() (bool, error) {
	return cgoPathWriteCatalogPrune()
}
