package cytindexer

// BuildPageIndexOnly builds page indexes without chunk variants.
// skillDirsJSON must be a JSON array of directory paths.
func BuildPageIndexOnly(skillDirsJSON, configJSON string) (string, error) {
	return cgoBuildPageIndexOnly(skillDirsJSON, configJSON)
}

// BuildChunkVariant builds a chunk variant for a skill entry directory.
func BuildChunkVariant(entryDir, docID, pipeline, paramsHash, configJSON string) (string, error) {
	return cgoBuildChunkVariant(entryDir, docID, pipeline, paramsHash, configJSON)
}

// PageIndexValid reports whether a valid page index exists for the content hash.
func PageIndexValid(entryDir, contentSHA256 string) (bool, error) {
	return cgoPageIndexValid(entryDir, contentSHA256)
}

// ChunkVariantValid reports whether a chunk variant exists for the pipeline/params hash.
func ChunkVariantValid(entryDir, docID, pipeline, paramsHash string) (bool, error) {
	return cgoChunkVariantValid(entryDir, docID, pipeline, paramsHash)
}

// RepairSkillVariantChunks repairs chunk variant files on disk.
func RepairSkillVariantChunks(entryDir, docID, pipeline, paramsHash, configJSON string) error {
	return cgoRepairSkillVariantChunks(entryDir, docID, pipeline, paramsHash, configJSON)
}

// LoadSkillsIndexFromEntry loads a merged skills index from an entry directory.
// Pass empty chunkDir when no chunk variant should be merged.
func LoadSkillsIndexFromEntry(entryDir, docID, chunkDir string) (string, error) {
	return cgoLoadSkillsIndexFromEntry(entryDir, docID, chunkDir)
}

// LoadMergedSkillDocumentJSON loads merged document metadata from disk.
func LoadMergedSkillDocumentJSON(entryDir, docID, chunkDir string) (string, error) {
	return cgoLoadMergedSkillDocumentJSON(entryDir, docID, chunkDir)
}

// FinalizeSkillDocumentJSON writes cache metadata into page_index.json.
func FinalizeSkillDocumentJSON(entryDir, docID, contentSHA256, pipeline, indexParamsJSON, builtAt, sourcePath string) (string, error) {
	return cgoFinalizeSkillDocumentJSON(entryDir, docID, contentSHA256, pipeline, indexParamsJSON, builtAt, sourcePath)
}

// UpdateSkillDocumentSourcePath updates the canonical source path in page_index.json.
func UpdateSkillDocumentSourcePath(entryDir, docID, sourcePath string) (string, error) {
	return cgoUpdateSkillDocumentSourcePath(entryDir, docID, sourcePath)
}
