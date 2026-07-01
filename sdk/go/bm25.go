package cytindexer

// DefaultBm25CohesionConfig returns the default BM25 cohesion config JSON.
func DefaultBm25CohesionConfig() (string, error) {
	return cgoBm25CohesionDefaultConfig()
}

// Bm25CohesionChunk segments text using BM25 cohesion.
func Bm25CohesionChunk(text, configJSON string) (string, error) {
	return cgoBm25CohesionChunk(text, configJSON)
}
