package e2esupport

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// safePathUnderRoot resolves path under repoRoot and returns a path rebuilt from
// root + relative segment so traversal outside the repo is rejected.
func safePathUnderRoot(path string) (string, error) {
	root := repoRoot()
	prefix := root + string(filepath.Separator)

	candidates := []string{path}
	if !filepath.IsAbs(path) {
		candidates = append(candidates, filepath.Join(root, path))
	}

	for _, candidate := range candidates {
		abs, err := filepath.Abs(candidate)
		if err != nil {
			continue
		}
		abs = filepath.Clean(abs)
		if abs != root && !strings.HasPrefix(abs, prefix) {
			continue
		}

		rel, err := filepath.Rel(root, abs)
		if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			continue
		}

		if rel == "." {
			return root, nil
		}
		return filepath.Join(root, rel), nil
	}

	return "", fmt.Errorf("path must stay under repo root %s: %s", root, path)
}

func resolveUnderRepo(path string) (string, error) {
	safe, err := safePathUnderRoot(path)
	if err != nil {
		return "", err
	}
	if _, err := os.Stat(safe); err != nil {
		return "", fmt.Errorf(
			"snapshot file not found under repo root %s: %s",
			repoRoot(),
			path,
		)
	}
	return safe, nil
}

func resolveOutputUnderRepo(path string) (string, error) {
	return safePathUnderRoot(path)
}
