package main

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"

	"github.com/qdrddr/clear-your-tools/sdk/go/moduleversion"
)

func TestResolveVersionUsesModuleVersion(t *testing.T) {
	t.Setenv("CYT_RELEASE_VERSION", "")
	if got := resolveVersion(""); got != moduleversion.Version {
		t.Fatalf("resolveVersion() = %q, want %q", got, moduleversion.Version)
	}
	if got := resolveVersion("v1.2.3"); got != "1.2.3" {
		t.Fatalf("resolveVersion(flag) = %q, want 1.2.3", got)
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

func TestLookupChecksum(t *testing.T) {
	sumData := []byte("abc123  cyt-indexer-ffi-aarch64-apple-darwin.tar.gz\n")
	got, err := lookupChecksum(sumData, "cyt-indexer-ffi-aarch64-apple-darwin.tar.gz")
	if err != nil {
		t.Fatal(err)
	}
	if got != "abc123" {
		t.Fatalf("lookupChecksum() = %q, want abc123", got)
	}
	if _, err := lookupChecksum(sumData, "missing.tar.gz"); err == nil {
		t.Fatal("expected error for missing archive")
	}
}

func TestVerifyDownloadSHA256(t *testing.T) {
	data := []byte("payload")
	sum := sha256.Sum256(data)
	expected := hex.EncodeToString(sum[:])
	if err := verifyDownloadSHA256(data, expected); err != nil {
		t.Fatalf("verifyDownloadSHA256() = %v", err)
	}
	if err := verifyDownloadSHA256(data, "deadbeef"); err == nil {
		t.Fatal("expected mismatch error")
	}
}

func TestCopyArtifactsIncludesSharedByDefault(t *testing.T) {
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

	if err := copyArtifacts(src, dest, triplet, false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dest, "libcyt_indexer.dylib")); err != nil {
		t.Fatal("expected shared lib when staticOnly is false")
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
		version:   moduleversion.Version,
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
