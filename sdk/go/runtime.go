package cytindexer

// ConfigureRuntimeDefaults sets global runtime scoring and policy defaults.
func ConfigureRuntimeDefaults(decomposedScore, enumScore, rerankScore float64, emptyOptionalFallbackK uint64, defaultSystemPolicy, defaultMcpPolicy string) error {
	return cgoConfigureRuntimeDefaults(decomposedScore, enumScore, rerankScore, emptyOptionalFallbackK, defaultSystemPolicy, defaultMcpPolicy)
}

// RuntimeDecomposedScore returns the current decomposed score default.
func RuntimeDecomposedScore() float64 {
	return cgoRuntimeDecomposedScore()
}

// RuntimeEnumScore returns the current enum score default.
func RuntimeEnumScore() float64 {
	return cgoRuntimeEnumScore()
}

// RuntimeRerankScore returns the current rerank score default.
func RuntimeRerankScore() float64 {
	return cgoRuntimeRerankScore()
}

// RuntimeEmptyOptionalFallbackK returns the empty-optional fallback K default.
func RuntimeEmptyOptionalFallbackK() uint64 {
	return cgoRuntimeEmptyOptionalFallbackK()
}

// RuntimeDefaultSystemPolicy returns the default system policy name.
func RuntimeDefaultSystemPolicy() (string, error) {
	return cgoRuntimeDefaultSystemPolicy()
}

// RuntimeDefaultMcpPolicy returns the default MCP policy name.
func RuntimeDefaultMcpPolicy() (string, error) {
	return cgoRuntimeDefaultMcpPolicy()
}
