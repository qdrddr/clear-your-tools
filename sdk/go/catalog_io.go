package cytindexer

// WriteCatalogIndex writes a catalog index JSON snapshot to a directory.
func WriteCatalogIndex(indexJSON, outputDir string, prune bool) error {
	return cgoWriteCatalogIndex(indexJSON, outputDir, prune)
}
