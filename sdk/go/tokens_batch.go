package cytindexer

// CountTokensBatch returns token counts for multiple strings as a JSON array.
func CountTokensBatch(textsJSON string) (string, error) {
	return cgoCountTokensBatch(textsJSON)
}
