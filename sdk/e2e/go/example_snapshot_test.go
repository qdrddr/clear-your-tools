package e2esupport

import (
	"encoding/json"
	"strings"
	"testing"

	cytindexer "github.com/qdrddr/clear-your-tools/sdk/go/v2"
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
	skipped, err := DecomposeFromExampleFile()
	if skipped {
		t.Skip("set CYT_E2E_FILE or pass --file after go test --")
	}
	if err != nil {
		t.Fatal(err)
	}
}
