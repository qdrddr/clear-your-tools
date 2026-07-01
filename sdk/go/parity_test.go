package cytindexer

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
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
