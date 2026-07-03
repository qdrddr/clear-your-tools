package cytindexer

// ClassifyOptionalChunksBatch classifies optional chunks for catalog items in one native call.
// itemsJSON must be a JSON array of catalog items. Returns JSON {"system":[bool,...],"mcp":[bool,...]}.
func ClassifyOptionalChunksBatch(itemsJSON string) (string, error) {
	return cgoClassifyOptionalChunksBatch(itemsJSON)
}
