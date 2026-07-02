package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestParseModuleVersionFile(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "version.go")
	if err := os.WriteFile(path, []byte(`const ModuleVersion = "1.2.3"`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := parseModuleVersionFile(path); got != "1.2.3" {
		t.Fatalf("parseModuleVersionFile() = %q, want 1.2.3", got)
	}
}

func TestIsSDKModuleRoot(t *testing.T) {
	dir := t.TempDir()
	goMod := filepath.Join(dir, "go.mod")
	if err := os.WriteFile(goMod, []byte("module github.com/qdrddr/clear-your-tools/sdk/go\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !isSDKModuleRoot(dir) {
		t.Fatal("expected sdk module root")
	}
	if isSDKModuleRoot(t.TempDir()) {
		t.Fatal("unexpected sdk module root for empty dir")
	}
}

func TestEnsureNativeDownloadsWhenCacheEmpty(t *testing.T) {
	if os.Getenv("CYT_NATIVE_ENSURE_INTEGRATION") != "1" {
		t.Skip("set CYT_NATIVE_ENSURE_INTEGRATION=1 to run release download test")
	}
	cacheRoot := t.TempDir()
	triplet, err := hostTriplet()
	if err != nil {
		t.Fatal(err)
	}
	dest, _, err := ensureNative(ensureConfig{
		version:   "0.6.4",
		repo:      defaultRepo,
		triplet:   triplet,
		cacheRoot: cacheRoot,
		force:     true,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !hasNativeLibs(dest, triplet) {
		t.Fatalf("expected native libs in %s", dest)
	}
}

func TestCopyArtifactsStaticOnly(t *testing.T) {
	src := t.TempDir()
	dest := t.TempDir()
	triplet := "aarch64-apple-darwin"

	for name, content := range map[string]string{
		"libcyt_indexer.a":     "static",
		"libcyt_indexer.dylib": "shared",
		"cyt_indexer.h":        "header",
	} {
		if err := os.WriteFile(filepath.Join(src, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	if err := copyArtifacts(src, dest, triplet, true); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dest, "libcyt_indexer.a")); err != nil {
		t.Fatal("missing static lib")
	}
	if _, err := os.Stat(filepath.Join(dest, "cyt_indexer.h")); err != nil {
		t.Fatal("missing header")
	}
	if _, err := os.Stat(filepath.Join(dest, "libcyt_indexer.dylib")); !os.IsNotExist(err) {
		t.Fatal("shared lib should be omitted with staticOnly")
	}
}
