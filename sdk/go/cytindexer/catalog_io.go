package cytindexer

import (
	"os"
	"path/filepath"
	"strings"
)

// ResolveWriteCatalogPaths resolves output directory and prune flag from optional overrides.
func ResolveWriteCatalogPaths(outputDir string, prune *bool) (string, bool) {
	cfg := PathSnapshot()
	dir := outputDir
	if dir == "" {
		dir = cfg.DefaultCatalogDir
	}
	doPrune := cfg.WriteCatalogPrune
	if prune != nil {
		doPrune = *prune
	}
	return dir, doPrune
}

// WriteCatalogIndexResolved writes a catalog index with optional dir/prune overrides.
func WriteCatalogIndexResolved(index *CatalogIndex, outputDir string, prune *bool) error {
	dir, doPrune := ResolveWriteCatalogPaths(outputDir, prune)
	return WriteCatalogIndex(index, dir, doPrune)
}

// WriteCatalogIndex writes catalog files to disk.
func WriteCatalogIndex(index *CatalogIndex, outputDir string, prune bool) error {
	if err := os.MkdirAll(outputDir, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(outputDir, "schemas"), 0o755); err != nil {
		return err
	}

	outputMap := make(map[string]string)
	for relPath, content := range index.Files {
		outputMap[filepath.Join(outputDir, relPath)] = content
	}

	if err := applyOutputs(outputMap); err != nil {
		return err
	}
	if prune {
		expected := make(map[string]struct{}, len(outputMap))
		for p := range outputMap {
			expected[p] = struct{}{}
		}
		return pruneStaleFiles(outputDir, expected)
	}
	return nil
}

func applyOutputs(outputMap map[string]string) error {
	for path, content := range outputMap {
		if existing, err := os.ReadFile(path); err == nil && string(existing) == content {
			continue
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			return err
		}
	}
	return nil
}

func shouldSkipHidden(path, root string) bool {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	for _, part := range strings.Split(filepath.ToSlash(rel), "/") {
		if strings.HasPrefix(part, ".") {
			return true
		}
	}
	return false
}

func pruneStaleFiles(root string, expected map[string]struct{}) error {
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return nil
	}

	var allPaths []string
	if err := collectPaths(root, root, &allPaths); err != nil {
		return err
	}

	for _, path := range allPaths {
		if shouldSkipHidden(path, root) {
			continue
		}
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			if _, ok := expected[path]; !ok {
				_ = os.Remove(path)
			}
		}
	}

	// Remove empty directories deepest-first.
	sortPathsByDepthDesc(allPaths)
	for _, path := range allPaths {
		if shouldSkipHidden(path, root) {
			continue
		}
		if info, err := os.Stat(path); err == nil && info.IsDir() {
			entries, _ := os.ReadDir(path)
			if len(entries) == 0 {
				_ = os.Remove(path)
			}
		}
	}
	return nil
}

func sortPathsByDepthDesc(paths []string) {
	for i := 0; i < len(paths); i++ {
		for j := i + 1; j < len(paths); j++ {
			if strings.Count(paths[j], string(os.PathSeparator)) > strings.Count(paths[i], string(os.PathSeparator)) {
				paths[i], paths[j] = paths[j], paths[i]
			}
		}
	}
}

func collectPaths(dir, root string, out *[]string) error {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		path := filepath.Join(dir, entry.Name())
		*out = append(*out, path)
		if entry.IsDir() {
			if err := collectPaths(path, root, out); err != nil {
				return err
			}
		}
	}
	return nil
}
