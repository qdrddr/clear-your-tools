package cytindexer

import "errors"

// BuildCatalogIndex builds a catalog index from tools and enums JSON arrays.
func BuildCatalogIndex(toolsJSON, enumsJSON string) (string, error) {
	return cgoBuildCatalogIndex(toolsJSON, enumsJSON)
}

// AnthropicToolsToCatalogEntries converts Anthropic tools JSON to catalog entries and enums.
func AnthropicToolsToCatalogEntries(toolsJSON string) (string, error) {
	return cgoAnthropicToolsToCatalogEntries(toolsJSON)
}

// BuildCatalogFromTools builds a catalog index from normalized tool entries JSON.
func BuildCatalogFromTools(toolsJSON string) (string, error) {
	return cgoBuildCatalogFromTools(toolsJSON)
}

// PrepareToolEntry prepares a single tool catalog entry JSON object.
func PrepareToolEntry(serverName, name, description, inputSchemaJSON string) (string, error) {
	return cgoPrepareToolEntry(serverName, name, description, inputSchemaJSON)
}

// AnthropicToolToCatalogEntry converts one Anthropic tool JSON to a catalog entry.
func AnthropicToolToCatalogEntry(toolJSON string) (string, error) {
	return cgoAnthropicToolToCatalogEntry(toolJSON)
}

// TruncateDescription truncates a tool description to a token budget.
func TruncateDescription(description string, maxTokens uint64) (string, error) {
	return cgoTruncateDescription(description, maxTokens)
}

// CatalogIndexToCatalogDict converts catalog index JSON to a catalog dict for retrieval.
func CatalogIndexToCatalogDict(indexJSON, catalogPrefix string) (string, error) {
	return cgoCatalogIndexToCatalogDict(indexJSON, catalogPrefix)
}

// CatalogBuilder is an opaque catalog builder handle.
type CatalogBuilder struct {
	handle catalogBuilderHandle
}

// NewCatalogBuilder creates a catalog builder.
func NewCatalogBuilder(memoryOnly bool, outputDir string) (*CatalogBuilder, error) {
	h, err := cgoCatalogBuilderNew(memoryOnly, outputDir)
	if err != nil {
		return nil, err
	}
	return &CatalogBuilder{handle: h}, nil
}

// Close frees the catalog builder handle.
func (b *CatalogBuilder) Close() {
	if b != nil && b.handle.h != nil {
		cgoCatalogBuilderFree(b.handle)
		b.handle = catalogBuilderHandle{}
	}
}

// AddTool adds a tool entry JSON object to the builder.
func (b *CatalogBuilder) AddTool(entryJSON string) error {
	if b == nil || b.handle.h == nil {
		return errors.New("closed catalog builder")
	}
	return cgoCatalogBuilderAddTool(b.handle, entryJSON)
}

// GetToolInfo returns tool info JSON for a server/tool pair.
func (b *CatalogBuilder) GetToolInfo(serverName, toolName string) (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed catalog builder")
	}
	return cgoCatalogBuilderGetToolInfo(b.handle, serverName, toolName)
}

// BuildIndex returns the in-memory catalog index JSON.
func (b *CatalogBuilder) BuildIndex() (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed catalog builder")
	}
	return cgoCatalogBuilderBuildIndex(b.handle)
}

// WriteCatalog writes the catalog to disk and returns metadata JSON.
func (b *CatalogBuilder) WriteCatalog() (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed catalog builder")
	}
	return cgoCatalogBuilderWriteCatalog(b.handle)
}

// ToCatalogDict converts the builder state to a catalog dict JSON.
func (b *CatalogBuilder) ToCatalogDict(catalogPrefix string) (string, error) {
	if b == nil || b.handle.h == nil {
		return "", errors.New("closed catalog builder")
	}
	return cgoCatalogBuilderToCatalogDict(b.handle, catalogPrefix)
}
