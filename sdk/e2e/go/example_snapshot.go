package e2esupport

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	cytindexer "github.com/qdrddr/clear-your-tools/sdk/go"
)

func repoRoot() string {
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		panic(err)
	}
	return root
}

func ParseTestArgs() (file *string, output *string) {
	if v := strings.TrimSpace(os.Getenv("CYT_E2E_FILE")); v != "" {
		file = &v
	} else if v := strings.TrimSpace(os.Getenv("FILE")); v != "" {
		file = &v
	}
	if v := strings.TrimSpace(os.Getenv("CYT_E2E_OUTPUT")); v != "" {
		output = &v
	} else if v := strings.TrimSpace(os.Getenv("OUTPUT")); v != "" {
		output = &v
	}
	if file != nil || output != nil {
		return file, output
	}

	args := os.Args
	for i := 0; i < len(args); i++ {
		switch {
		case args[i] == "--file" && i+1 < len(args):
			i++
			v := args[i]
			file = &v
		case strings.HasPrefix(args[i], "--file="):
			v := strings.TrimPrefix(args[i], "--file=")
			file = &v
		case args[i] == "--output" && i+1 < len(args):
			i++
			v := args[i]
			output = &v
		case strings.HasPrefix(args[i], "--output="):
			v := strings.TrimPrefix(args[i], "--output=")
			output = &v
		}
	}
	return file, output
}

func ResolveSnapshotPath(path string) string {
	if _, err := os.Stat(path); err == nil {
		return path
	}
	fromRepo := filepath.Join(repoRoot(), path)
	if _, err := os.Stat(fromRepo); err == nil {
		return fromRepo
	}
	panic("snapshot file not found: " + path + " (also tried " + fromRepo + ")")
}

func LoadSnapshot(path string) map[string]any {
	raw, err := os.ReadFile(path)
	if err != nil {
		panic("read " + path + ": " + err.Error())
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		panic("parse " + path + ": " + err.Error())
	}
	return data
}

func enumsFromMD(mdEntries []any) []string {
	enums := make([]string, 0, len(mdEntries))
	for _, entry := range mdEntries {
		obj, ok := entry.(map[string]any)
		if !ok {
			continue
		}
		content, ok := obj["content"].(string)
		if ok {
			enums = append(enums, content)
		}
	}
	return enums
}

func survivorCatalog(stage map[string]any) map[string]any {
	survivor := map[string]any{}
	if v, ok := stage["json"]; ok {
		survivor["json"] = v
	}
	if v, ok := stage["md"]; ok {
		survivor["md"] = v
	}
	return survivor
}

func asObject(v any) map[string]any {
	obj, _ := v.(map[string]any)
	if obj == nil {
		return map[string]any{}
	}
	return obj
}

func asArray(v any) []any {
	arr, _ := v.([]any)
	if arr == nil {
		return []any{}
	}
	return arr
}

func ExtractSnapshotParts(data map[string]any) ([]any, map[string]any, []any) {
	pruning := asObject(data["pruning"])
	stages := asObject(pruning["decomposed_catalog"])

	var expected, buildStage, survivorStage map[string]any
	if _, ok := data["body"]; ok {
		body := asObject(data["body"])
		expected = map[string]any{"tools": asArray(body["tools"])}
		buildStage = asObject(stages["build_index"])
		if v, ok := stages["rerank"]; ok {
			survivorStage = asObject(v)
		} else {
			survivorStage = buildStage
		}
	} else {
		expected = map[string]any{"tools": asArray(data["tools"])}
		buildStage = asObject(stages["build_index"])
		if stages["json"] != nil || stages["md"] != nil {
			survivorStage = stages
		} else {
			survivorStage = buildStage
		}
	}

	buildTools := asArray(buildStage["tools"])
	if len(buildTools) == 0 && len(asArray(expected["tools"])) > 0 {
		panic("snapshot has no pruning.decomposed_catalog.build_index.tools; cannot rebuild catalog index")
	}

	survivor := survivorCatalog(survivorStage)
	hasJSON := len(asArray(survivor["json"])) > 0
	hasMD := len(asArray(survivor["md"])) > 0
	if !hasJSON && !hasMD {
		panic("snapshot has no rerank json/md entries for decomposition")
	}

	return buildTools, survivor, asArray(expected["tools"])
}

func CatalogDictFromSnapshot(data map[string]any) (map[string]any, error) {
	buildTools, _, _ := ExtractSnapshotParts(data)
	pruning := asObject(data["pruning"])
	stages := asObject(pruning["decomposed_catalog"])
	buildStage := asObject(stages["build_index"])
	mdEntries := asArray(buildStage["md"])

	enumsJSON, err := json.Marshal(enumsFromMD(mdEntries))
	if err != nil {
		return nil, err
	}
	toolsJSON, err := json.Marshal(buildTools)
	if err != nil {
		return nil, err
	}
	indexJSON, err := cytindexer.BuildCatalogIndex(string(toolsJSON), string(enumsJSON))
	if err != nil {
		return nil, err
	}
	dictJSON, err := cytindexer.CatalogIndexToCatalogDict(indexJSON, "")
	if err != nil {
		return nil, err
	}
	var catalog map[string]any
	if err := json.Unmarshal([]byte(dictJSON), &catalog); err != nil {
		return nil, err
	}
	return catalog, nil
}

func WriteOutput(catalog map[string]any, outputPath *string) error {
	payload, err := json.MarshalIndent(catalog, "", "  ")
	if err != nil {
		return err
	}
	payload = append(payload, '\n')
	if outputPath == nil {
		_, err = os.Stdout.Write(payload)
		return err
	}
	if err := os.MkdirAll(filepath.Dir(*outputPath), 0o755); err != nil && !os.IsExist(err) {
		return err
	}
	return os.WriteFile(*outputPath, payload, 0o644)
}
