package cytindexer

import "errors"

// SkillsBuilder is an opaque skills index builder handle.
type SkillsBuilder struct {
	handle skillsBuilderHandle
}

// NewSkillsBuilder creates a skills builder.
func NewSkillsBuilder(memoryOnly bool, outputDir string) (*SkillsBuilder, error) {
	h, err := cgoSkillsBuilderNew(memoryOnly, outputDir)
	if err != nil {
		return nil, err
	}
	return &SkillsBuilder{handle: h}, nil
}

// Close frees the skills builder handle.
func (b *SkillsBuilder) Close() {
	if b != nil && b.handle.h != nil {
		cgoSkillsBuilderFree(b.handle)
		b.handle = skillsBuilderHandle{}
	}
}

// BuildFromDirs builds a skills index from skill directory paths JSON.
func (b *SkillsBuilder) BuildFromDirs(skillDirsJSON, configJSON string) (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed skills builder")
	}
	return cgoSkillsBuilderBuildFromDirs(b.handle, skillDirsJSON, configJSON)
}

// WriteCatalog writes the skills catalog to disk.
func (b *SkillsBuilder) WriteCatalog() (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed skills builder")
	}
	return cgoSkillsBuilderWriteCatalog(b.handle)
}

// ToSkillsIndexJSON returns the in-memory skills index JSON.
func (b *SkillsBuilder) ToSkillsIndexJSON() (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed skills builder")
	}
	return cgoSkillsBuilderToSkillsIndexJSON(b.handle)
}

// ToSkillsDict returns the skills dict JSON.
func (b *SkillsBuilder) ToSkillsDict() (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed skills builder")
	}
	return cgoSkillsBuilderToSkillsDict(b.handle)
}

// BuildSkillsIndex builds a skills index from skill directories JSON.
func BuildSkillsIndex(skillDirsJSON, configJSON string) (string, error) {
	return cgoBuildSkillsIndex(skillDirsJSON, configJSON)
}

// WriteSkillsIndex writes a skills index JSON snapshot to a directory.
func WriteSkillsIndex(indexJSON, outputDir string) error {
	return cgoWriteSkillsIndex(indexJSON, outputDir)
}

// LoadSkillsIndexFromDir loads a skills index from a catalog directory.
func LoadSkillsIndexFromDir(catalogDir string) (string, error) {
	return cgoLoadSkillsIndexFromDir(catalogDir)
}

// RepairSkillChunks repairs skill chunks on disk for a document id.
func RepairSkillChunks(entryDir, docID, configJSON string) error {
	return cgoRepairSkillChunks(entryDir, docID, configJSON)
}

// SkillsIndexFromDecomposedDir rebuilds a skills index from a decomposed catalog directory.
func SkillsIndexFromDecomposedDir(dir string) (string, error) {
	return cgoSkillsIndexFromDecomposedDir(dir)
}

// MdToTree parses markdown into a page-index tree JSON.
func MdToTree(markdownContent, sourcePath, configJSON string) (string, error) {
	return cgoMdToTree(markdownContent, sourcePath, configJSON)
}

// GetSkillDocument returns skill document metadata JSON.
func GetSkillDocument(documentsJSON, docID string) (string, error) {
	return cgoGetSkillDocument(documentsJSON, docID)
}

// GetSkillStructure returns skill document structure JSON.
func GetSkillStructure(documentsJSON, docID string) (string, error) {
	return cgoGetSkillStructure(documentsJSON, docID)
}

// GetSkillLineContentFromSpec retrieves line content from a line-number spec.
func GetSkillLineContentFromSpec(indexOrDocsJSON, docID, lineNumSpec string) (string, error) {
	return cgoGetSkillLineContentFromSpec(indexOrDocsJSON, docID, lineNumSpec)
}

// GetSkillContentRetrieveResult retrieves skill content using line/node/chunk specs.
func GetSkillContentRetrieveResult(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) (string, error) {
	return cgoGetSkillContentRetrieveResult(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON)
}

// ReconstructSkillMarkdown reconstructs skill markdown from specs.
func ReconstructSkillMarkdown(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) (string, error) {
	return cgoReconstructSkillMarkdown(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON)
}

// WriteReconstructedSkill writes reconstructed skill markdown to disk.
func WriteReconstructedSkill(catalogDir, indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON string) error {
	return cgoWriteReconstructedSkill(catalogDir, indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON, optionsJSON)
}

// GetSkillLineContent retrieves skill line content from specs.
func GetSkillLineContent(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON string) (string, error) {
	return cgoGetSkillLineContent(indexOrDocsJSON, docID, lineNumSpecsJSON, nodeIDSpecsJSON, chunkIDSpecsJSON)
}

// ParseSkillChunkIDs parses a chunk id spec string into a JSON array.
func ParseSkillChunkIDs(spec string) (string, error) {
	return cgoParseSkillChunkIDs(spec)
}

// ParseSkillNodeIDs parses a node id spec string into a JSON array.
func ParseSkillNodeIDs(spec string) (string, error) {
	return cgoParseSkillNodeIDs(spec)
}

// TokenCountFromDecomposedFrontmatter parses token_count from decomposed frontmatter.
// The second return value is false when token_count is absent.
func TokenCountFromDecomposedFrontmatter(content string) (int64, bool, error) {
	return cgoTokenCountFromDecomposedFrontmatter(content)
}

// ReconstructOptionsDefault returns default reconstruct options JSON.
func ReconstructOptionsDefault() (string, error) {
	return cgoReconstructOptionsDefault()
}
