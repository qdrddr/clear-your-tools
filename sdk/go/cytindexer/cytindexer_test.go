package cytindexer

import (
	"testing"
)

func TestBuildSimpleTool(t *testing.T) {
	tool := map[string]any{
		"id": "mcp__test__foo", "server": "test", "tool": "mcp__test__foo", "summary": "A test tool",
		"full_schema": map[string]any{
			"id": "mcp__test__foo", "name": "mcp__test__foo", "description": "A test tool",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"required_field": map[string]any{"type": "string"},
					"optional_field": map[string]any{"type": "string", "description": "opt"},
				},
				"required": []any{"required_field"},
			},
		},
	}
	index := BuildCatalogFromTools([]any{tool})
	if _, ok := index.Files["schemas/decomposed/mcp__test__foo.json"]; !ok {
		t.Fatal("missing root decomposed file")
	}
	foundOptional := false
	for k := range index.Files {
		if contains(k, "optional_field") {
			foundOptional = true
		}
	}
	if !foundOptional {
		t.Fatal("missing optional_field chunk")
	}
}

func TestBuildFromAnthropicTools(t *testing.T) {
	tool := map[string]any{
		"name": "Agent", "description": "Launch agents",
		"input_schema": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"prompt": map[string]any{"type": "string"},
				"model":  map[string]any{"type": "string", "enum": []any{"opus", "haiku"}},
			},
			"required": []any{"prompt"},
		},
	}
	index := BuildCatalogFromTools([]any{tool})
	if _, ok := index.Files["schemas/decomposed/Agent.json"]; !ok {
		t.Fatal("missing Agent root")
	}
	foundModel := false
	for k := range index.Files {
		if contains(k, "Agent/model") {
			foundModel = true
		}
	}
	if !foundModel {
		t.Fatal("missing model optional chunk")
	}
	if _, ok := index.Files["schemas/decomposed/haiku.md"]; !ok {
		t.Fatal("missing haiku enum md")
	}
}

func TestEnumMDFilesWithoutJSONQuotes(t *testing.T) {
	index := BuildCatalogIndex(nil, []any{"Bash", "auto"})
	if got := index.Files["schemas/decomposed/Bash.md"]; got != "Bash" {
		t.Fatalf("Bash.md = %q, want Bash", got)
	}
	if got := index.Files["schemas/decomposed/auto.md"]; got != "auto" {
		t.Fatalf("auto.md = %q, want auto", got)
	}
}

func TestToolPolicyRoundtrip(t *testing.T) {
	for _, s := range ToolPolicyStrings() {
		p, ok := ToolPolicyFromString(s)
		if !ok {
			t.Fatalf("failed to parse %s", s)
		}
		if p.String() != s {
			t.Fatalf("%s != %s", p.String(), s)
		}
	}
}

func TestMCPToolIDDetection(t *testing.T) {
	if !IsNonSystemToolID("mcp__foo") {
		t.Fatal("expected mcp tool")
	}
	if IsSystemToolID("mcp__foo") {
		t.Fatal("mcp tool should not be system")
	}
}

func TestParseToolPolicyPairValid(t *testing.T) {
	tool, policy, err := ParseToolPolicyPair("Agent=always_include")
	if err != nil {
		t.Fatal(err)
	}
	if tool != "Agent" || policy != PolicyAlwaysInclude {
		t.Fatalf("got %s %v", tool, policy)
	}
}

func TestLowRerankScoresKeptWithoutScoreFilter(t *testing.T) {
	data := map[string]any{
		"json": []any{map[string]any{
			"file_path": "schemas/decomposed/Agent.json",
			"score":     "0.003",
		}},
	}
	files := ExtractInputFiles(data, false)
	if len(files) != 1 {
		t.Fatalf("want 1 file, got %d", len(files))
	}
}

func TestLowRerankScoresDroppedWithScoreFilter(t *testing.T) {
	data := map[string]any{
		"json": []any{map[string]any{
			"file_path": "schemas/decomposed/Agent.json",
			"score":     "0.003",
		}},
	}
	files := ExtractInputFiles(data, true)
	if len(files) != 0 {
		t.Fatalf("want 0 files, got %d", len(files))
	}
}

func TestTruncateShortTextUnchanged(t *testing.T) {
	text := "short tool description"
	if got := TruncateDescription(text, 60); got != text {
		t.Fatalf("got %q", got)
	}
}

func TestDefaultPrefixRoundTrip(t *testing.T) {
	ConfigurePaths(DefaultPathConfig())
	rel := DecomposedPrefix() + "tool.json"
	if got := ToolIDFromDecomposedRel(rel); got != "tool" {
		t.Fatalf("got %q", got)
	}
}

func contains(s, sub string) bool {
	return len(s) >= len(sub) && (s == sub || len(sub) == 0 || indexOf(s, sub) >= 0)
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}
