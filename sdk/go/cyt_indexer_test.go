package cytindexer

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestBuildCatalogIndexSmoke(t *testing.T) {
	indexJSON, err := BuildCatalogIndex("[]", "[]")
	if err != nil {
		t.Fatalf("BuildCatalogIndex: %v", err)
	}
	if !strings.Contains(indexJSON, `"tools"`) {
		t.Fatalf("expected tools key in index JSON: %s", indexJSON)
	}
}

func TestCatalogToolCountSmoke(t *testing.T) {
	count, err := CatalogToolCount(`{"json":[],"md":[]}`)
	if err != nil {
		t.Fatalf("CatalogToolCount: %v", err)
	}
	if count != 0 {
		t.Fatalf("expected 0 tools, got %d", count)
	}
}

func TestToolPoliciesSmoke(t *testing.T) {
	policiesJSON, err := ToolPolicies()
	if err != nil {
		t.Fatalf("ToolPolicies: %v", err)
	}
	var policies []string
	if err := json.Unmarshal([]byte(policiesJSON), &policies); err != nil {
		t.Fatalf("unmarshal policies: %v", err)
	}
	if len(policies) != 5 {
		t.Fatalf("expected 5 policies, got %d", len(policies))
	}
}

func TestMdToTreeSmoke(t *testing.T) {
	treeJSON, err := MdToTree("# Title\n\nBody", "skill.md", "{}")
	if err != nil {
		t.Fatalf("MdToTree: %v", err)
	}
	if !strings.Contains(treeJSON, "Title") {
		t.Fatalf("expected title in tree JSON: %s", treeJSON)
	}
}

func TestParseSkillChunkIDsSmoke(t *testing.T) {
	idsJSON, err := ParseSkillChunkIDs("8-10")
	if err != nil {
		t.Fatalf("ParseSkillChunkIDs: %v", err)
	}
	var ids []int
	if err := json.Unmarshal([]byte(idsJSON), &ids); err != nil {
		t.Fatalf("unmarshal chunk ids: %v", err)
	}
	if len(ids) != 3 || ids[0] != 8 || ids[2] != 10 {
		t.Fatalf("unexpected ids: %v", ids)
	}
}

func TestRuntimeDefaultsSmoke(t *testing.T) {
	if err := ConfigureRuntimeDefaults(0.5, 0.2, 0.003, 3, "prune_optional", "prune_all"); err != nil {
		t.Fatalf("ConfigureRuntimeDefaults: %v", err)
	}
	if RuntimeDecomposedScore() != 0.5 {
		t.Fatalf("unexpected decomposed score: %v", RuntimeDecomposedScore())
	}
}

func TestCountTokensSmoke(t *testing.T) {
	count, err := CountTokens("hello world")
	if err != nil {
		t.Fatalf("CountTokens: %v", err)
	}
	if count < 1 {
		t.Fatalf("expected token count >= 1, got %d", count)
	}
}

func TestBm25ScoreCatalogSmoke(t *testing.T) {
	catalog := `{"json":[{"file_path":"a.json","content":{"name":"read files from disk"}}],"md":[{"file_path":"b.md","content":"write disk files"}]}`
	scoredJSON, err := Bm25ScoreCatalog(catalog, "read files disk", "")
	if err != nil {
		t.Fatalf("Bm25ScoreCatalog: %v", err)
	}
	if !strings.Contains(scoredJSON, `"score"`) {
		t.Fatalf("expected score field in result: %s", scoredJSON)
	}
}
