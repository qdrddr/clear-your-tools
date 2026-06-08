package cytindexer

import (
	"path/filepath"
	"strings"
	"sync"
)

// PathConfig holds SDK runtime defaults for paths and catalog I/O.
type PathConfig struct {
	JSONExt            string
	MDExt              string
	DecomposedPrefix   string
	DecomposedRoot     string
	CatalogPrefix      string
	BuilderMemoryOnly  bool
	DefaultCatalogDir  string
	WriteCatalogPrune  bool
}

// DefaultPathConfig returns the default path configuration.
func DefaultPathConfig() PathConfig {
	return PathConfig{
		JSONExt:           ".json",
		MDExt:             ".md",
		DecomposedPrefix:  "schemas/decomposed/",
		DecomposedRoot:    "schemas/decomposed",
		CatalogPrefix:     "catalog",
		BuilderMemoryOnly: false,
		DefaultCatalogDir: "catalog",
		WriteCatalogPrune: true,
	}
}

var (
	pathMu     sync.RWMutex
	pathConfig = DefaultPathConfig()
)

// ConfigurePaths overrides path configuration.
func ConfigurePaths(cfg PathConfig) {
	pathMu.Lock()
	pathConfig = cfg
	pathMu.Unlock()
}

// PathSnapshot returns a copy of the current path configuration.
func PathSnapshot() PathConfig {
	pathMu.RLock()
	defer pathMu.RUnlock()
	return pathConfig
}

func jsonExt() string     { return PathSnapshot().JSONExt }
func mdExt() string       { return PathSnapshot().MDExt }
func decomposedPrefix() string { return PathSnapshot().DecomposedPrefix }
func decomposedRoot() string   { return PathSnapshot().DecomposedRoot }

// JSONExt returns the configured JSON file extension.
func JSONExt() string { return jsonExt() }

// MDExt returns the configured markdown file extension.
func MDExt() string { return mdExt() }

// DecomposedPrefix returns the decomposed schema directory prefix.
func DecomposedPrefix() string { return decomposedPrefix() }

// DecomposedRoot returns the decomposed schema root path.
func DecomposedRoot() string { return decomposedRoot() }

// CatalogPrefix returns the catalog prefix string.
func CatalogPrefix() string { return PathSnapshot().CatalogPrefix }

// DefaultCatalogDir returns the default on-disk catalog directory.
func DefaultCatalogDir() string { return PathSnapshot().DefaultCatalogDir }

// WriteCatalogPrune returns whether stale catalog files should be pruned on write.
func WriteCatalogPrune() bool { return PathSnapshot().WriteCatalogPrune }

// ToDecomposedKey normalizes a file path to a decomposed catalog key.
func ToDecomposedKey(filePath string) (string, bool) {
	clean := filepath.ToSlash(filepath.Clean(filePath))
	parts := strings.Split(clean, "/")
	for i := 0; i < len(parts)-1; i++ {
		if parts[i] == "schemas" && parts[i+1] == "decomposed" {
			return strings.Join(parts[i:], "/"), true
		}
	}
	return "", false
}

// ToolIDFromDecomposedRel extracts the tool id from a decomposed relative path.
func ToolIDFromDecomposedRel(relPath string) string {
	cfg := PathSnapshot()
	rel := relPath
	if strings.HasPrefix(rel, cfg.DecomposedPrefix) {
		rel = strings.TrimPrefix(rel, cfg.DecomposedPrefix)
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) == 0 {
		base := filepath.Base(rel)
		return strings.TrimSuffix(base, cfg.JSONExt)
	}
	first := parts[0]
	if strings.HasSuffix(first, cfg.JSONExt) {
		return strings.TrimSuffix(first, cfg.JSONExt)
	}
	return first
}

// GetRootToolKey returns the root decomposed key for a catalog file path.
func GetRootToolKey(filePath string) (string, bool) {
	cfg := PathSnapshot()
	key, ok := ToDecomposedKey(filePath)
	if !ok {
		return "", false
	}
	rel, err := filepath.Rel(cfg.DecomposedRoot, key)
	if err != nil || rel == "." || rel == "" {
		return "", false
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	if len(parts) == 1 && strings.HasSuffix(parts[0], cfg.JSONExt) {
		return key, true
	}
	toolID := parts[0]
	return cfg.DecomposedPrefix + toolID + cfg.JSONExt, true
}

// CollectEnums recursively collects enum values from a JSON schema.
func CollectEnums(schema any) []any {
	var found []any
	collectEnumsInner(schema, &found)
	return found
}

func collectEnumsInner(node any, found *[]any) {
	switch n := node.(type) {
	case map[string]any:
		if items, ok := AsArray(n["enum"]); ok {
			*found = append(*found, items...)
		}
		for _, val := range n {
			if _, isObj := AsObject(val); isObj {
				collectEnumsInner(val, found)
			} else if _, isArr := AsArray(val); isArr {
				collectEnumsInner(val, found)
			}
		}
	case []any:
		for _, item := range n {
			if _, isObj := AsObject(item); isObj {
				collectEnumsInner(item, found)
			} else if _, isArr := AsArray(item); isArr {
				collectEnumsInner(item, found)
			}
		}
	}
}
