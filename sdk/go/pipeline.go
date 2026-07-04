package cytindexer

// PruneCatalogBm25AndRetrieve partitions, BM25-scores, recomposes, and retrieves tools.
func PruneCatalogBm25AndRetrieve(
	catalogJSON, buildCatalogJSON, catalogIndexJSON, query, scoringCtxJSON, outputCtxJSON, optionsJSON string,
) (string, error) {
	return cgoPruneCatalogBm25AndRetrieve(
		catalogJSON,
		buildCatalogJSON,
		catalogIndexJSON,
		query,
		scoringCtxJSON,
		outputCtxJSON,
		optionsJSON,
	)
}

// ClassifyAndCountCatalog classifies optional chunks and optionally counts tool tokens.
func ClassifyAndCountCatalog(catalogJSON, toolsJSON string) (string, error) {
	return cgoClassifyAndCountCatalog(catalogJSON, toolsJSON)
}

// SearchSkillsAndSelect runs BM25 skill search with optional budget selection.
func SearchSkillsAndSelect(entriesJSON, query, optionsJSON string) (string, error) {
	return cgoSearchSkillsAndSelect(entriesJSON, query, optionsJSON)
}

// BuildSkillNodeCatalog batch-loads rerankable node bodies for skill entries.
func BuildSkillNodeCatalog(entriesJSON string) (string, error) {
	return cgoBuildSkillNodeCatalog(entriesJSON)
}
