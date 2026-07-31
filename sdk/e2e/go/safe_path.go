package e2esupport

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func isUnderRoot(path, root string) bool {
	rel, err := filepath.Rel(root, path)
	if err != nil {
		return false
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

func resolveUnderRepo(path string) (string, error) {
	root := repoRoot()
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
		if !isUnderRoot(abs, root) {
			continue
		}
		if _, err := os.Stat(abs); err == nil {
			return abs, nil
		}
	}

	return "", fmt.Errorf(
		"snapshot file not found under repo root %s: %s",
		root,
		path,
	)
}

func resolveOutputUnderRepo(path string) (string, error) {
	root := repoRoot()
	abs, err := filepath.Abs(path)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)
	if !isUnderRoot(abs, root) {
		return "", fmt.Errorf("output path must stay under repo root %s, got %s", root, abs)
	}
	return abs, nil
}
