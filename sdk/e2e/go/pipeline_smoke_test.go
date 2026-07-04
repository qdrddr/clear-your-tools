package e2esupport_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	cytindexer "github.com/qdrddr/clear-your-tools/sdk/go"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "../../.."))
}

func loadFixture(t *testing.T, name string) string {
	t.Helper()
	path := filepath.Join(repoRoot(t), "sdk", "e2e", "fixtures", name)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	return string(data)
}

func TestBatchToolPassThroughFromFixture(t *testing.T) {
	ctx := `{"system_policy":"always_include","mcp_policy":"always_include"}`
	toolIDs := `["Agent","grep"]`
	got, err := cytindexer.BatchToolPassThrough(ctx, toolIDs)
	if err != nil {
		t.Fatalf("BatchToolPassThrough: %v", err)
	}
	if !strings.Contains(got, "true") {
		t.Fatalf("expected pass-through flags, got %s", got)
	}
}

func TestClassifyAndCountCatalogFromFixture(t *testing.T) {
	catalog := loadFixture(t, "bm25_catalog.json")
	got, err := cytindexer.ClassifyAndCountCatalog(catalog, "")
	if err != nil {
		t.Fatalf("ClassifyAndCountCatalog: %v", err)
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(got), &parsed); err != nil {
		t.Fatalf("invalid JSON: %v\n%s", err, got)
	}
	if _, ok := parsed["optional_chunk_count"]; !ok {
		t.Fatalf("missing optional_chunk_count in %s", got)
	}
}

func TestClassifyOptionalChunksBatchSmoke(t *testing.T) {
	items := `[{"file_path":"schemas/decomposed/mcp__test__read.json"}]`
	got, err := cytindexer.ClassifyOptionalChunksBatch(items)
	if err != nil {
		t.Fatalf("ClassifyOptionalChunksBatch: %v", err)
	}
	if !strings.Contains(got, `"system"`) || !strings.Contains(got, `"mcp"`) {
		t.Fatalf("unexpected batch classify JSON: %s", got)
	}
}

func TestToolPassThroughSmoke(t *testing.T) {
	ctx := `{"system_policy":"always_include","mcp_policy":"always_include"}`
	ok, err := cytindexer.ToolPassThrough(ctx, "Agent")
	if err != nil {
		t.Fatalf("ToolPassThrough: %v", err)
	}
	if !ok {
		t.Fatal("expected Agent to pass through with always_include policies")
	}
}
