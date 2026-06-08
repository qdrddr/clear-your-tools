package cytindexer

import "sync"

// RuntimeConfig holds score thresholds and default policy strings.
type RuntimeConfig struct {
	DecomposedScore          float64
	EnumScore                float64
	RerankScore              float64
	EmptyOptionalFallbackK   int
	DefaultSystemPolicy      string
	DefaultMCPPolicy         string
}

// DefaultRuntimeConfig returns the default runtime configuration.
func DefaultRuntimeConfig() RuntimeConfig {
	return RuntimeConfig{
		DecomposedScore:        0.5,
		EnumScore:              0.2,
		RerankScore:            0.003,
		EmptyOptionalFallbackK: 3,
		DefaultSystemPolicy:    "prune_optional",
		DefaultMCPPolicy:       "prune_all",
	}
}

var (
	runtimeMu     sync.RWMutex
	runtimeConfig = DefaultRuntimeConfig()
)

// ConfigureRuntime overrides runtime configuration.
func ConfigureRuntime(cfg RuntimeConfig) {
	runtimeMu.Lock()
	runtimeConfig = cfg
	runtimeMu.Unlock()
}

// RuntimeSnapshot returns a copy of the current runtime configuration.
func RuntimeSnapshot() RuntimeConfig {
	runtimeMu.RLock()
	defer runtimeMu.RUnlock()
	return runtimeConfig
}

func decomposedScore() float64        { return RuntimeSnapshot().DecomposedScore }
func enumScore() float64              { return RuntimeSnapshot().EnumScore }
func rerankScore() float64            { return RuntimeSnapshot().RerankScore }
func emptyOptionalFallbackK() int     { return RuntimeSnapshot().EmptyOptionalFallbackK }
func defaultSystemPolicy() string     { return RuntimeSnapshot().DefaultSystemPolicy }
func defaultMCPPolicy() string        { return RuntimeSnapshot().DefaultMCPPolicy }

// DecomposedScore returns the decomposed chunk score threshold.
func DecomposedScore() float64 { return decomposedScore() }

// EnumScore returns the enum pruning score threshold.
func EnumScore() float64 { return enumScore() }

// RerankScore returns the rerank survivor score threshold.
func RerankScore() float64 { return rerankScore() }

// EmptyOptionalFallbackK returns how many optional chunks to keep as fallback.
func EmptyOptionalFallbackK() int { return emptyOptionalFallbackK() }
