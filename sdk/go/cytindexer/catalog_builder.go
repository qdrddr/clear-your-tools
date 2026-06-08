package cytindexer

// CatalogBuilder incrementally builds a decomposed catalog index.
type CatalogBuilder struct {
	Tools       []any
	AllEnums    []any
	index       *CatalogIndex
	lookup      map[[2]string]int
	MemoryOnly  bool
	OutputDir   string
}

// NewCatalogBuilder creates a builder with explicit memory-only and output dir settings.
func NewCatalogBuilder(memoryOnly bool, outputDir string) *CatalogBuilder {
	return NewCatalogBuilderWithOptions(&memoryOnly, outputDir)
}

// NewCatalogBuilderWithOptions creates a builder using optional memory-only override.
func NewCatalogBuilderWithOptions(memoryOnly *bool, outputDir string) *CatalogBuilder {
	cfg := PathSnapshot()
	mo := cfg.BuilderMemoryOnly
	if memoryOnly != nil {
		mo = *memoryOnly
	}
	return &CatalogBuilder{
		lookup:     make(map[[2]string]int),
		MemoryOnly: mo,
		OutputDir:  outputDir,
	}
}

// AddTool adds a catalog tool entry.
func (b *CatalogBuilder) AddTool(entry any) {
	obj, _ := AsObject(entry)
	server := StrField(obj, "server")
	tool := StrField(obj, "tool")
	idx := len(b.Tools)
	b.lookup[[2]string{server, tool}] = idx
	if fs, ok := AsObject(obj["full_schema"]); ok {
		if schema, ok := fs["inputSchema"]; ok {
			b.AllEnums = append(b.AllEnums, CollectEnums(schema)...)
		} else if schema, ok := fs["input_schema"]; ok {
			b.AllEnums = append(b.AllEnums, CollectEnums(schema)...)
		}
	}
	b.Tools = append(b.Tools, entry)
	b.index = nil
}

// GetToolInfo returns a tool entry by server and tool name.
func (b *CatalogBuilder) GetToolInfo(serverName, toolName string) (any, bool) {
	idx, ok := b.lookup[[2]string{serverName, toolName}]
	if !ok || idx >= len(b.Tools) {
		return nil, false
	}
	return b.Tools[idx], true
}

// BuildIndex builds and caches the catalog index.
func (b *CatalogBuilder) BuildIndex() *CatalogIndex {
	if b.index == nil {
		idx := BuildCatalogIndex(b.Tools, b.AllEnums)
		b.index = &idx
	}
	return b.index
}

// WriteCatalog builds and optionally writes the catalog to disk.
func (b *CatalogBuilder) WriteCatalog() (*CatalogIndex, error) {
	index := b.BuildIndex()
	if !b.MemoryOnly {
		dir := b.OutputDir
		if dir == "" {
			dir = DefaultCatalogDir()
		}
		if err := WriteCatalogIndex(index, dir, WriteCatalogPrune()); err != nil {
			return nil, err
		}
	}
	return b.index, nil
}

// ToCatalogDict returns the catalog as a dictionary.
func (b *CatalogBuilder) ToCatalogDict() map[string]any {
	return b.BuildIndex().ToCatalogDict()
}

// ToCatalogDictWithPrefix returns the catalog dict with a custom prefix.
func (b *CatalogBuilder) ToCatalogDictWithPrefix(catalogPrefix string) map[string]any {
	return b.BuildIndex().ToCatalogDictWithPrefix(catalogPrefix)
}
