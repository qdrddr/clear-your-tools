package cytindexer

// ExtractDocumentText extracts plain text from a catalog document item JSON.
func ExtractDocumentText(itemJSON string) (string, error) {
	return cgoExtractDocumentText(itemJSON)
}

// ExtractLevelInfo extracts heading level metadata from a catalog document item.
func ExtractLevelInfo(itemJSON string) (string, error) {
	return cgoExtractLevelInfo(itemJSON)
}

// ExtractJSONCatalogDocument extracts JSON catalog document fields.
func ExtractJSONCatalogDocument(itemJSON string) (string, error) {
	return cgoExtractJSONCatalogDocument(itemJSON)
}

// ExtractMdCatalogDocument extracts markdown catalog document fields.
func ExtractMdCatalogDocument(itemJSON string) (string, error) {
	return cgoExtractMdCatalogDocument(itemJSON)
}
