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
