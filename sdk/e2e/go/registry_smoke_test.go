package e2esupport_test

import (
	"encoding/json"
	"strings"
	"testing"

	cytindexer "github.com/qdrddr/clear-your-tools/sdk/go/v2"

	e2esupport "cyt-indexer-go-registry-e2e"
)

func TestBuildCatalogIndexFromReleaseModule(t *testing.T) {
	tool := map[string]any{
		"id":      "mcp__test__foo",
		"server":  "test",
		"tool":    "mcp__test__foo",
		"summary": "A test tool",
		"full_schema": map[string]any{
			"id":          "mcp__test__foo",
			"name":        "mcp__test__foo",
			"description": "A test tool",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"required_field": map[string]any{"type": "string"},
					"optional_field": map[string]any{
						"type":        "string",
						"description": "opt",
					},
				},
				"required": []any{"required_field"},
			},
		},
	}
	toolsJSON, err := json.Marshal([]any{tool})
	if err != nil {
		t.Fatalf("marshal tool: %v", err)
	}

	indexJSON, err := cytindexer.BuildCatalogIndex(string(toolsJSON), "[]")
	if err != nil {
		t.Fatalf("BuildCatalogIndex: %v", err)
	}
	if !strings.Contains(indexJSON, "schemas/decomposed/mcp__test__foo.json") {
		t.Fatalf("expected decomposed path in index JSON: %s", indexJSON)
	}
}

func TestDecomposeFromExampleFile(t *testing.T) {
	exampleFile, outputFile := e2esupport.ParseTestArgs()
	if exampleFile == nil {
		t.Skip("set CYT_E2E_FILE or pass --file after go test --")
	}

	snapshotPath := e2esupport.ResolveSnapshotPath(*exampleFile)
	data := e2esupport.LoadSnapshot(snapshotPath)
	_, _, _ = e2esupport.ExtractSnapshotParts(data)

	catalog, err := e2esupport.CatalogDictFromSnapshot(data)
	if err != nil {
		t.Fatalf("catalog from snapshot: %v", err)
	}

	jsonChunks, _ := catalog["json"].([]any)
	mdChunks, _ := catalog["md"].([]any)
	if len(jsonChunks) == 0 {
		t.Fatal("build_catalog_index produced no json chunks")
	}
	if len(mdChunks) == 0 {
		t.Fatal("build_catalog_index produced no md enum chunks")
	}

	foundDecomposed := false
	for _, entry := range jsonChunks {
		obj, ok := entry.(map[string]any)
		if !ok {
			continue
		}
		path, _ := obj["file_path"].(string)
		if strings.Contains(path, "/schemas/decomposed/") && strings.HasSuffix(path, ".json") {
			foundDecomposed = true
			break
		}
	}
	if !foundDecomposed {
		t.Fatal("expected per-property decomposed json chunks")
	}

	if err := e2esupport.WriteOutput(catalog, outputFile); err != nil {
		t.Fatalf("write output: %v", err)
	}
}
