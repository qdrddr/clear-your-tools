package cytindexer

// CountTokens returns the tiktoken count for text under the configured encoding.
func CountTokens(text string) (int64, error) {
	return cgoCountTokens(text)
}

// CountJsonTokens returns the token count for compact JSON text.
func CountJsonTokens(jsonStr string) (int64, error) {
	return cgoCountJsonTokens(jsonStr)
}

// ConfigureTokenizerDefaults overrides tokenizer defaults in the native core.
// Pass empty configJSON to leave current settings unchanged.
func ConfigureTokenizerDefaults(configJSON string) error {
	return cgoConfigureTokenizerDefaults(configJSON)
}
