package cytindexer

import (
	"encoding/json"
	"path/filepath"
	"sort"
	"strings"
)

// CatalogIndex holds decomposed catalog tools and file contents.
type CatalogIndex struct {
	Tools []any
	Files map[string]string
}

// CatalogIndexFromValue parses a catalog index from `{tools, files}` JSON.
func CatalogIndexFromValue(val any) CatalogIndex {
	idx := CatalogIndex{Files: make(map[string]string)}
	obj, ok := AsObject(val)
	if !ok {
		return idx
	}
	if tools, ok := AsArray(obj["tools"]); ok {
		idx.Tools = tools
	}
	if files, ok := AsObject(obj["files"]); ok {
		for k, v := range files {
			if s, ok := v.(string); ok {
				idx.Files[k] = s
			}
		}
	}
	return idx
}

// ToCatalogDict converts the index to a catalog dictionary.
func (c *CatalogIndex) ToCatalogDict() map[string]any {
	return c.ToCatalogDictWithPrefix(CatalogPrefix())
}

// ToCatalogDictWithPrefix converts the index with a custom catalog prefix.
func (c *CatalogIndex) ToCatalogDictWithPrefix(catalogPrefix string) map[string]any {
	var paths []string
	for p := range c.Files {
		paths = append(paths, p)
	}
	sort.Strings(paths)

	var mdEntries []any
	var jsonEntries []any
	prefix := decomposedPrefix()
	jExt := jsonExt()
	mExt := mdExt()

	for _, relPath := range paths {
		if !strings.HasPrefix(relPath, prefix) {
			continue
		}
		content := c.Files[relPath]
		filePath := catalogPrefix + "/" + relPath
		if strings.HasSuffix(relPath, mExt) {
			id := strings.TrimSuffix(filepath.Base(relPath), mExt)
			mdEntries = append(mdEntries, map[string]any{
				"id": id, "file_path": filePath, "score": 1.0,
				"start_line": 1, "end_line": 1, "language": "markdown", "content": content,
			})
		} else if strings.HasSuffix(relPath, jExt) {
			var parsed any
			if err := json.Unmarshal([]byte(content), &parsed); err != nil {
				continue
			}
			if _, ok := AsObject(parsed); !ok {
				continue
			}
			lineCount := strings.Count(content, "\n") + 1
			entryID := parsed.(map[string]any)["id"]
			if entryID == nil {
				entryID = ToolIDFromDecomposedRel(relPath)
			}
			jsonEntries = append(jsonEntries, map[string]any{
				"id": entryID, "name": entryID, "file_path": filePath, "score": 1.0,
				"start_line": 1, "end_line": lineCount, "language": "json", "content": parsed,
			})
		}
	}
	return map[string]any{"md": mdEntries, "json": jsonEntries, "tools": c.Tools}
}

// CatalogToolCount counts tools in a catalog dict.
func CatalogToolCount(data any) int {
	obj, ok := AsObject(data)
	if !ok {
		return 0
	}
	if tools, ok := AsArray(obj["tools"]); ok && len(tools) > 0 {
		return len(tools)
	}
	jsonItems, ok := AsArray(obj["json"])
	if !ok {
		return 0
	}
	toolIDs := make(map[string]struct{})
	for _, item := range jsonItems {
		entry, ok := AsObject(item)
		if !ok {
			continue
		}
		if fp := StrField(entry, "file_path"); fp != "" {
			toolIDs[ToolIDFromDecomposedRel(fp)] = struct{}{}
			continue
		}
		id := StrField(entry, "id")
		if id == "" {
			id = StrField(entry, "name")
		}
		if id != "" {
			toolIDs[id] = struct{}{}
		}
	}
	return len(toolIDs)
}

func enumMarkdownValue(val any) string {
	return ValueToString(val)
}

// DedupeEnums deduplicates and sorts enum values.
func DedupeEnums(allEnums []any) []any {
	seen := make(map[string]struct{})
	var unique []any
	for _, val := range allEnums {
		key, _ := json.Marshal(val)
		if _, ok := seen[string(key)]; ok {
			continue
		}
		seen[string(key)] = struct{}{}
		unique = append(unique, CloneValue(val))
	}
	sort.Slice(unique, func(i, j int) bool {
		ki, _ := json.Marshal(unique[i])
		kj, _ := json.Marshal(unique[j])
		return string(ki) < string(kj)
	})
	return unique
}

type pathSegment map[string]any
type extraction struct {
	path   []pathSegment
	schema any
}

func segment(segType string, extra map[string]any) pathSegment {
	m := map[string]any{"type": segType}
	for k, v := range extra {
		m[k] = v
	}
	return m
}

func buildPropertyFile(toolName string, path []pathSegment, leafSchema any) map[string]any {
	current := CloneValue(leafSchema)
	for i := len(path) - 1; i >= 0; i-- {
		seg := path[i]
		segType := StrField(seg, "type")
		switch segType {
		case "properties":
			name := StrField(seg, "name")
			current = map[string]any{"properties": map[string]any{name: current}}
		case "items":
			if _, hasIndex := seg["index"]; hasIndex {
				current = map[string]any{"items": []any{current}}
			} else {
				current = map[string]any{"items": current}
			}
		case "allOf", "anyOf", "oneOf":
			current = map[string]any{segType: []any{current}}
		case "additionalProperties":
			current = map[string]any{"additionalProperties": current}
		case "patternProperties":
			pat := StrField(seg, "pattern")
			current = map[string]any{"patternProperties": map[string]any{pat: current}}
		case "if", "then", "else", "not", "contains", "propertyNames":
			current = map[string]any{segType: current}
		}
	}
	return map[string]any{
		"id": toolName, "name": toolName, "inputSchema": current,
	}
}

func processNode(node any, toolName, serverName string, path []pathSegment, extractions *[]extraction) any {
	obj, ok := AsObject(node)
	if !ok {
		return node
	}
	result := CloneValue(obj).(map[string]any)
	processCompositions(result, toolName, serverName, path, extractions)

	if props, ok := AsObject(result["properties"]); ok {
		reqSet := make(map[string]struct{})
		if req, ok := AsArray(result["required"]); ok {
			for _, r := range req {
				if s, ok := r.(string); ok {
					reqSet[s] = struct{}{}
				}
			}
		}
		filtered := make(map[string]any)
		for propName, propSchema := range props {
			childPath := append(append([]pathSegment{}, path...), segment("properties", map[string]any{"name": propName}))
			if _, required := reqSet[propName]; required {
				filtered[propName] = processNode(propSchema, toolName, serverName, childPath, extractions)
			} else {
				filteredChild := processNode(propSchema, toolName, serverName, childPath, extractions)
				propFile := buildPropertyFile(toolName, childPath, filteredChild)
				*extractions = append(*extractions, extraction{path: childPath, schema: propFile})
			}
		}
		result["properties"] = filtered
	}
	return result
}

func processCompositions(result map[string]any, toolName, serverName string, path []pathSegment, extractions *[]extraction) {
	handleLogicalCompositions(result, toolName, serverName, path, extractions)
	handleConditionalCompositions(result, toolName, serverName, path, extractions)
	handleArrayProperties(result, toolName, serverName, path, extractions)
	handleMiscellaneousKeywords(result, toolName, serverName, path, extractions)
}

func handleLogicalCompositions(result map[string]any, toolName, serverName string, path []pathSegment, extractions *[]extraction) {
	for _, key := range []string{"allOf", "anyOf", "oneOf"} {
		items, ok := AsArray(result[key])
		if !ok {
			continue
		}
		processed := make([]any, len(items))
		for i, item := range items {
			p := append(append([]pathSegment{}, path...), segment(key, map[string]any{"index": float64(i)}))
			processed[i] = processNode(item, toolName, serverName, p, extractions)
		}
		result[key] = processed
	}
}

func handleConditionalCompositions(result map[string]any, toolName, serverName string, path []pathSegment, extractions *[]extraction) {
	for _, key := range []string{"if", "then", "else"} {
		if val, ok := result[key]; ok {
			p := append(append([]pathSegment{}, path...), segment(key, nil))
			result[key] = processNode(val, toolName, serverName, p, extractions)
		}
	}
	if val, ok := result["not"]; ok {
		p := append(append([]pathSegment{}, path...), segment("not", nil))
		result["not"] = processNode(val, toolName, serverName, p, extractions)
	}
}

func handleArrayProperties(result map[string]any, toolName, serverName string, path []pathSegment, extractions *[]extraction) {
	items, ok := result["items"]
	if !ok {
		return
	}
	if obj, ok := AsObject(items); ok {
		p := append(append([]pathSegment{}, path...), segment("items", nil))
		result["items"] = processNode(obj, toolName, serverName, p, extractions)
	} else if arr, ok := AsArray(items); ok {
		processed := make([]any, len(arr))
		for i, item := range arr {
			p := append(append([]pathSegment{}, path...), segment("items", map[string]any{"index": float64(i)}))
			processed[i] = processNode(item, toolName, serverName, p, extractions)
		}
		result["items"] = processed
	}
}

func handleMiscellaneousKeywords(result map[string]any, toolName, serverName string, path []pathSegment, extractions *[]extraction) {
	for _, key := range []string{"contains", "propertyNames", "additionalProperties"} {
		if obj, ok := AsObject(result[key]); ok {
			p := append(append([]pathSegment{}, path...), segment(key, nil))
			result[key] = processNode(obj, toolName, serverName, p, extractions)
		}
	}
	if pp, ok := AsObject(result["patternProperties"]); ok {
		newPP := make(map[string]any)
		for pat, sub := range pp {
			p := append(append([]pathSegment{}, path...), segment("patternProperties", map[string]any{"pattern": pat}))
			newPP[pat] = processNode(sub, toolName, serverName, p, extractions)
		}
		result["patternProperties"] = newPP
	}
}

// DecomposeToolSchema splits a tool entry into root schema and optional property extractions.
func DecomposeToolSchema(toolInfo any) (any, []extraction) {
	obj, _ := AsObject(toolInfo)
	toolID := StrField(obj, "id")
	var tDesc string
	var tSchema any = nil
	if fs, ok := AsObject(obj["full_schema"]); ok {
		tDesc = StrField(fs, "description")
		if s, ok := fs["inputSchema"]; ok {
			tSchema = s
		} else if s, ok := fs["input_schema"]; ok {
			tSchema = s
		}
	}
	server := StrField(obj, "server")

	var extractions []extraction
	var filtered any
	if _, ok := AsObject(tSchema); ok {
		filtered = processNode(tSchema, toolID, server, nil, &extractions)
	} else {
		filtered = tSchema
	}
	rootSchema := map[string]any{
		"id": toolID, "name": toolID, "description": tDesc, "inputSchema": filtered,
	}
	return rootSchema, extractions
}

func propertyRelativePath(toolID string, pathSegments []pathSegment, propName string) string {
	prefix := strings.TrimSuffix(decomposedPrefix(), "/")
	parts := []string{prefix, toolID}
	for i := 0; i < len(pathSegments)-1; i++ {
		seg := pathSegments[i]
		segType := StrField(seg, "type")
		switch segType {
		case "properties":
			if name := StrField(seg, "name"); name != "" {
				parts = append(parts, name)
			}
		case "patternProperties":
			if pat := StrField(seg, "pattern"); pat != "" {
				parts = append(parts, pat)
			}
		}
	}
	parts = append(parts, propName+jsonExt())
	return strings.Join(parts, "/")
}

// BuildCatalogIndex builds a decomposed catalog index from tool entries and enums.
func BuildCatalogIndex(tools []any, allEnums []any) CatalogIndex {
	files := make(map[string]string)
	jExt := jsonExt()
	prefix := decomposedPrefix()
	mExt := mdExt()

	for _, toolInfo := range tools {
		obj, _ := AsObject(toolInfo)
		toolID := StrField(obj, "id")
		if fs, ok := obj["full_schema"]; ok {
			b, _ := json.MarshalIndent(fs, "", "  ")
			files["schemas/full/"+toolID+jExt] = string(b)
		}
	}

	for _, val := range DedupeEnums(allEnums) {
		text := enumMarkdownValue(val)
		files[prefix+text+mExt] = text
	}

	for _, toolInfo := range tools {
		obj, _ := AsObject(toolInfo)
		toolID := StrField(obj, "id")
		rootSchema, extractions := DecomposeToolSchema(toolInfo)
		b, _ := json.MarshalIndent(rootSchema, "", "  ")
		files[prefix+toolID+jExt] = string(b)
		for _, ext := range extractions {
			propName := ""
			if len(ext.path) > 0 {
				propName = StrField(ext.path[len(ext.path)-1], "name")
			}
			relPath := propertyRelativePath(toolID, ext.path, propName)
			eb, _ := json.MarshalIndent(ext.schema, "", "  ")
			files[relPath] = string(eb)
		}
	}

	tb, _ := json.MarshalIndent(tools, "", "  ")
	files["tools.json"] = string(tb)

	return CatalogIndex{Tools: tools, Files: files}
}
