package cytindexer

// DefaultBm25CohesionConfig returns the default BM25 cohesion config JSON.
func DefaultBm25CohesionConfig() (string, error) {
	return cgoBm25CohesionDefaultConfig()
}

// Bm25CohesionChunk segments text using BM25 cohesion.
func Bm25CohesionChunk(text, configJSON string) (string, error) {
	return cgoBm25CohesionChunk(text, configJSON)
}

// ConfigureBm25Defaults overrides BM25 search defaults in the native core.
// Pass empty configJSON to leave current settings unchanged.
func ConfigureBm25Defaults(configJSON string) error {
	return cgoConfigureBm25Defaults(configJSON)
}

// Bm25CatalogFingerprint hashes catalog documents plus analyzer settings.
func Bm25CatalogFingerprint(dataJSON string) (string, error) {
	return cgoBm25CatalogFingerprint(dataJSON)
}

// Bm25ScoreCatalog scores catalog json/md lists in-place and returns updated catalog JSON.
func Bm25ScoreCatalog(dataJSON, query, optionsJSON string) (string, error) {
	return cgoBm25ScoreCatalog(dataJSON, query, optionsJSON)
}

// Bm25FrontmatterGate returns excluded entry refs and trace metadata JSON.
func Bm25FrontmatterGate(entriesJSON, query string, upperLimit float64) (string, error) {
	return cgoBm25FrontmatterGate(entriesJSON, query, upperLimit)
}

// Bm25SearchSkillChunks searches skill chunks and returns matches + trace JSON.
func Bm25SearchSkillChunks(entriesJSON, query string, threshold float64, excludedJSON string) (string, error) {
	return cgoBm25SearchSkillChunks(entriesJSON, query, threshold, excludedJSON)
}
