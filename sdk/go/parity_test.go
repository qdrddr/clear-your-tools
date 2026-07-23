package cytindexer

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"testing"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("runtime.Caller failed")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "../.."))
}

func pythonAvailable(t *testing.T) bool {
	t.Helper()
	root := repoRoot(t)
	if _, err := exec.LookPath("uv"); err != nil {
		return false
	}
	cmd := exec.Command("uv", "run", "python", "-c", "import cyt_indexer")
	cmd.Dir = root
	return cmd.Run() == nil
}

func pythonJSON(t *testing.T, script string) string {
	t.Helper()
	root := repoRoot(t)
	cmd := exec.Command("uv", "run", "python", "-c", script)
	cmd.Dir = root
	out, err := cmd.Output()
	if err != nil {
		t.Fatalf("python reference failed: %v\n%s", err, cmd.Stderr)
	}
	return string(out)
}

func assertJSONEqual(t *testing.T, got, want string) {
	t.Helper()
	var gotVal any
	var wantVal any
	if err := json.Unmarshal([]byte(got), &gotVal); err != nil {
		t.Fatalf("got JSON invalid: %v\n%s", err, got)
	}
	if err := json.Unmarshal([]byte(want), &wantVal); err != nil {
		t.Fatalf("want JSON invalid: %v\n%s", err, want)
	}
	gotBytes, _ := json.Marshal(gotVal)
	wantBytes, _ := json.Marshal(wantVal)
	if string(gotBytes) != string(wantBytes) {
		t.Fatalf("JSON mismatch\ngot:  %s\nwant: %s", gotBytes, wantBytes)
	}
}

func TestParityBuildCatalogIndex(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available (run uv sync at repo root)")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import build_catalog_index
print(json.dumps(build_catalog_index([], [])))
`)

	got, err := BuildCatalogIndex("[]", "[]")
	if err != nil {
		t.Fatalf("BuildCatalogIndex: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityToolPolicies(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import tool_policies
print(json.dumps(tool_policies()))
`)

	got, err := ToolPolicies()
	if err != nil {
		t.Fatalf("ToolPolicies: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityParseSkillChunkIDs(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import parse_skill_chunk_ids
print(json.dumps(parse_skill_chunk_ids("8-10")))
`)

	got, err := ParseSkillChunkIDs("8-10")
	if err != nil {
		t.Fatalf("ParseSkillChunkIDs: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityBm25CohesionChunk(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer import bm25_cohesion_chunk, Bm25CohesionConfig
text = "Alpha one two three. Beta finance market stocks."
print(json.dumps(bm25_cohesion_chunk(text, Bm25CohesionConfig(chunk_size=2048))))
`)

	cfg, err := DefaultBm25CohesionConfig()
	if err != nil {
		t.Fatalf("DefaultBm25CohesionConfig: %v", err)
	}
	got, err := Bm25CohesionChunk("Alpha one two three. Beta finance market stocks.", cfg)
	if err != nil {
		t.Fatalf("Bm25CohesionChunk: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityExpSimilarity(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer import exp_similarity
print(json.dumps(exp_similarity(2.5)))
`)

	gotBytes, err := json.Marshal(ExpSimilarity(2.5))
	if err != nil {
		t.Fatalf("marshal got: %v", err)
	}
	assertJSONEqual(t, string(gotBytes), want)
}

func TestParityBatchToolPassThrough(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import policy_context_from_values, batch_tool_pass_through
cfg = {"pruning": {"tools": {"policy": {"system_tool": "always_include", "mcp_tool": "always_include"}}}}
ctx = policy_context_from_values(cfg)
print(json.dumps(batch_tool_pass_through(ctx, ["Agent", "grep"])))
`)

	ctx := `{"system_policy":"always_include","mcp_policy":"always_include"}`
	got, err := BatchToolPassThrough(ctx, `["Agent","grep"]`)
	if err != nil {
		t.Fatalf("BatchToolPassThrough: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityToolPassThrough(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import policy_context_from_values, tool_pass_through
cfg = {"pruning": {"tools": {"policy": {"system_tool": "always_include", "mcp_tool": "always_include"}}}}
ctx = policy_context_from_values(cfg)
print(json.dumps(tool_pass_through(ctx, "Agent")))
`)

	ctx := `{"system_policy":"always_include","mcp_policy":"always_include"}`
	got, err := ToolPassThrough(ctx, "Agent")
	if err != nil {
		t.Fatalf("ToolPassThrough: %v", err)
	}
	gotBytes, _ := json.Marshal(got)
	assertJSONEqual(t, string(gotBytes), want)
}

func TestParityClassifyOptionalChunksBatch(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import classify_optional_chunks_batch
items = [{"file_path": "schemas/decomposed/mcp__test__read.json"}]
print(json.dumps(classify_optional_chunks_batch(items)))
`)

	items := `[{"file_path":"schemas/decomposed/mcp__test__read.json"}]`
	got, err := ClassifyOptionalChunksBatch(items)
	if err != nil {
		t.Fatalf("ClassifyOptionalChunksBatch: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityClassifyAndCountCatalog(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import classify_and_count_catalog
catalog = {"json": [{"file_path": "schemas/decomposed/mcp__test__read.json", "content": "Read files"}], "md": []}
print(json.dumps(classify_and_count_catalog(catalog, None)))
`)

	catalog := `{"json":[{"file_path":"schemas/decomposed/mcp__test__read.json","content":"Read files"}],"md":[]}`
	got, err := ClassifyAndCountCatalog(catalog, "")
	if err != nil {
		t.Fatalf("ClassifyAndCountCatalog: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityEffectivePolicyToolKind(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import PolicyContext, effective_policy
ctx = PolicyContext()
ctx.system_policy = "prune_optional"
ctx.mcp_policy = "prune_all"
ctx.tool_kind = "mcp"
print(json.dumps(effective_policy(ctx, "tools.demo.org.search")))
`)

	kind := ToolKindMcp
	ctx := PolicyContext{
		SystemPolicy: "prune_optional",
		McpPolicy:    "prune_all",
		ToolKind:     &kind,
	}
	ctxJSON, err := ctx.MarshalJSONString()
	if err != nil {
		t.Fatalf("MarshalJSONString: %v", err)
	}
	got, err := EffectivePolicy(ctxJSON, "tools.demo.org.search")
	if err != nil {
		t.Fatalf("EffectivePolicy: %v", err)
	}
	gotBytes, err := json.Marshal(got)
	if err != nil {
		t.Fatalf("marshal got: %v", err)
	}
	assertJSONEqual(t, string(gotBytes), want)
}

func TestScoringPolicyContextCopiesToolKind(t *testing.T) {
	kind := ToolKindMcp
	ctx := PolicyContext{
		SystemPolicy: "prune_optional_descriptions",
		McpPolicy:    "prune_all_descriptions",
		ToolKind:     &kind,
	}
	scoring, err := ScoringPolicyContext(ctx)
	if err != nil {
		t.Fatalf("ScoringPolicyContext: %v", err)
	}
	if scoring.ToolKind == nil || *scoring.ToolKind != ToolKindMcp {
		t.Fatalf("expected tool_kind mcp, got %#v", scoring.ToolKind)
	}
	if scoring.SystemPolicy != "prune_optional" || scoring.McpPolicy != "prune_all" {
		t.Fatalf("unexpected scoring policies: %#v", scoring)
	}
}

func TestParityCoordinateBm25Prune(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import policy_context_from_values, coordinate_bm25_prune
ctx = policy_context_from_values({})
catalog = {"json": [], "md": []}
index = {"tools": [], "files": {}}
print(json.dumps(coordinate_bm25_prune([], catalog, catalog, index, "hello", ctx, ctx)))
`)

	ctx := `{}`
	catalog := `{"json":[],"md":[]}`
	index := `{"tools":[],"files":{}}`
	got, err := CoordinateBm25Prune("[]", catalog, catalog, index, "hello", ctx, ctx, "")
	if err != nil {
		t.Fatalf("CoordinateBm25Prune: %v", err)
	}
	assertJSONEqual(t, got, want)
}


func TestParityRecomposeAndRetrieveTools(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer._native import policy_context_from_values, recompose_and_retrieve_tools
ctx = policy_context_from_values({})
data = {"json": [], "md": []}
catalog = {"json": [], "md": []}
index = {"tools": [], "files": {}}
print(json.dumps(recompose_and_retrieve_tools(data, catalog, index, None, None, None, [], ctx, ctx)))
`)

	ctx := `{}`
	data := `{"json":[],"md":[]}`
	catalog := `{"json":[],"md":[]}`
	index := `{"tools":[],"files":{}}`
	got, err := RecomposeAndRetrieveTools(data, catalog, index, "", "", "", "[]", ctx, ctx)
	if err != nil {
		t.Fatalf("RecomposeAndRetrieveTools: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityTokenCountFromDecomposedFrontmatter(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	content := "---\ndoc_id: d1\nnode_id: 2\ntoken_count: 42\n---\n## Body\n"
	want := pythonJSON(t, fmt.Sprintf(`
import json
from cyt_indexer import token_count_from_decomposed_frontmatter
content = %s
print(json.dumps(token_count_from_decomposed_frontmatter(content)))
`, strconv.Quote(content)))

	got, ok, err := TokenCountFromDecomposedFrontmatter(content)
	if err != nil {
		t.Fatalf("TokenCountFromDecomposedFrontmatter: %v", err)
	}
	if !ok {
		t.Fatal("expected token_count in frontmatter")
	}
	gotBytes, _ := json.Marshal(got)
	assertJSONEqual(t, string(gotBytes), want)
}

func TestParityCatalogIndexToolSchemaMetadata(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer import catalog_index_tool_schema_metadata
print(json.dumps(catalog_index_tool_schema_metadata({"tools": [], "files": {}})))
`)

	got, err := CatalogIndexToolSchemaMetadata(`{"tools":[],"files":{}}`)
	if err != nil {
		t.Fatalf("CatalogIndexToolSchemaMetadata: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityBuildSkillNodeCatalogEmpty(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer import build_skill_node_catalog
print(json.dumps(build_skill_node_catalog([])))
`)

	got, err := BuildSkillNodeCatalog("[]")
	if err != nil {
		t.Fatalf("BuildSkillNodeCatalog: %v", err)
	}
	assertJSONEqual(t, got, want)
}

func TestParityGetVersion(t *testing.T) {
	if os.Getenv("CYT_SKIP_PARITY") == "1" {
		t.Skip("CYT_SKIP_PARITY=1")
	}
	if !pythonAvailable(t) {
		t.Skip("python cyt_indexer not available")
	}

	want := pythonJSON(t, `
import json
from cyt_indexer import get_version
print(json.dumps(get_version()))
`)

	got, err := Version()
	if err != nil {
		t.Fatalf("Version: %v", err)
	}
	gotBytes, _ := json.Marshal(got)
	assertJSONEqual(t, string(gotBytes), want)
}
