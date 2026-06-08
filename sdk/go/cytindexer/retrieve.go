package cytindexer

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// DecomposedCatalog holds decomposed JSON schema files keyed by relative path.
type DecomposedCatalog struct {
	jsonFiles map[string]any
}

// NewDecomposedCatalog creates an empty decomposed catalog.
func NewDecomposedCatalog() *DecomposedCatalog {
	return &DecomposedCatalog{jsonFiles: make(map[string]any)}
}

// FromJSONFiles creates a catalog from a json-files map.
func FromJSONFiles(jsonFiles map[string]any) *DecomposedCatalog {
	files := make(map[string]any, len(jsonFiles))
	for k, v := range jsonFiles {
		files[k] = CloneValue(v)
	}
	return &DecomposedCatalog{jsonFiles: files}
}

// JSONFiles returns the underlying json file map.
func (d *DecomposedCatalog) JSONFiles() map[string]any {
	return d.jsonFiles
}

// FromCatalogIndex builds a decomposed catalog from a catalog index.
func FromCatalogIndex(index *CatalogIndex) *DecomposedCatalog {
	files := make(map[string]any)
	prefix := decomposedPrefix()
	jExt := jsonExt()
	for relPath, content := range index.Files {
		if strings.HasPrefix(relPath, prefix) && strings.HasSuffix(relPath, jExt) {
			var parsed any
			if err := json.Unmarshal([]byte(content), &parsed); err == nil {
				if _, ok := AsObject(parsed); ok {
					files[relPath] = parsed
				}
			}
		}
	}
	return &DecomposedCatalog{jsonFiles: files}
}

// FromCatalogDict builds a decomposed catalog from a catalog dictionary.
func FromCatalogDict(data any) *DecomposedCatalog {
	files := make(map[string]any)
	obj, ok := AsObject(data)
	if !ok {
		return &DecomposedCatalog{jsonFiles: files}
	}
	if entries, ok := AsArray(obj["json"]); ok {
		for _, entry := range entries {
			eobj, ok := AsObject(entry)
			if !ok {
				continue
			}
			filePath := StrField(eobj, "file_path")
			content := eobj["content"]
			if filePath == "" {
				continue
			}
			if _, ok := AsObject(content); !ok {
				continue
			}
			if key, ok := ToDecomposedKey(filePath); ok {
				files[key] = CloneValue(content)
			}
		}
	}
	return &DecomposedCatalog{jsonFiles: files}
}

// DecomposedCatalogFromValue parses a host catalog value into DecomposedCatalog.
func DecomposedCatalogFromValue(val any) *DecomposedCatalog {
	obj, ok := AsObject(val)
	if ok && obj["tools"] != nil && obj["files"] != nil {
		idx := CatalogIndexFromValue(val)
		return FromCatalogIndex(&idx)
	}
	if obj != nil {
		files := make(map[string]any)
		for k, v := range obj {
			if _, ok := AsObject(v); ok {
				files[k] = CloneValue(v)
			}
		}
		if len(files) > 0 {
			return FromJSONFiles(files)
		}
	}
	return NewDecomposedCatalog()
}

// MergeJSONFiles merges another catalog's json files.
func (d *DecomposedCatalog) MergeJSONFiles(other *DecomposedCatalog) {
	for k, v := range other.jsonFiles {
		d.jsonFiles[k] = CloneValue(v)
	}
}

// ResolveKey resolves a file path to a catalog key.
func (d *DecomposedCatalog) ResolveKey(filePath string) string {
	if key, ok := ToDecomposedKey(filePath); ok && d.HasJSON(key) {
		return key
	}
	if d.HasJSON(filePath) {
		return filePath
	}
	if key, ok := ToDecomposedKey(filePath); ok {
		return key
	}
	return filePath
}

// HasJSON reports whether a key exists.
func (d *DecomposedCatalog) HasJSON(key string) bool {
	_, ok := d.jsonFiles[key]
	return ok
}

// GetJSON returns a json file by key.
func (d *DecomposedCatalog) GetJSON(key string) (any, bool) {
	v, ok := d.jsonFiles[key]
	return v, ok
}

// DeepMerge deep-merges two JSON values.
func DeepMerge(base, override any) any {
	baseObj, baseOK := AsObject(base)
	overrideObj, overrideOK := AsObject(override)
	if baseOK && overrideOK {
		result := CloneValue(baseObj).(map[string]any)
		for key, val := range overrideObj {
			if existing, ok := result[key]; ok {
				if _, eObj := AsObject(existing); eObj {
					if _, vObj := AsObject(val); vObj {
						result[key] = DeepMerge(existing, val)
						continue
					}
				}
			}
			result[key] = CloneValue(val)
		}
		return result
	}
	return CloneValue(override)
}

// ClimbAndMerge merges a leaf path up through parent schemas.
func ClimbAndMerge(leafPath string, catalog *DecomposedCatalog) any {
	leafKey := catalog.ResolveKey(leafPath)
	if !catalog.HasJSON(leafKey) {
		if key, ok := ToDecomposedKey(leafPath); ok {
			leafKey = key
		} else {
			leafKey = leafPath
		}
	}
	current, ok := catalog.GetJSON(leafKey)
	if !ok {
		return map[string]any{}
	}
	current = CloneValue(current)

	currentPath := filepath.ToSlash(leafKey)
	root := decomposedRoot()

	for {
		parentDir := filepath.Dir(currentPath)
		if parentDir == root || !strings.HasPrefix(parentDir, root) {
			break
		}
		baseName := filepath.Base(currentPath)
		parentKey := parentDir + "/" + baseName + jsonExt()
		if parent, ok := catalog.GetJSON(parentKey); ok {
			current = DeepMerge(parent, current)
			currentPath = parentDir
		} else {
			currentPath = parentDir
		}
	}
	return current
}

// ExtractScores extracts scores from catalog survivor data.
func ExtractScores(data any) map[string]float64 {
	scores := make(map[string]float64)
	obj, ok := AsObject(data)
	if !ok {
		return scores
	}
	if md, ok := AsArray(obj["md"]); ok {
		for _, entry := range md {
			if e, ok := AsObject(entry); ok {
				content, _ := e["content"].(string)
				if score, ok := JSONF64(e["score"]); ok && content != "" {
					scores[content] = score
				}
			}
		}
	}
	if jsonArr, ok := AsArray(obj["json"]); ok {
		for _, entry := range jsonArr {
			if e, ok := AsObject(entry); ok {
				fp := StrField(e, "file_path")
				if score, ok := JSONF64(e["score"]); ok && fp != "" {
					scores[fp] = score
				}
			}
		}
	}
	return scores
}

func extractFromDict(data map[string]any, applyDecomposedScoreFilter bool) []string {
	var inputFiles []string
	for key, value := range data {
		if key == "md" {
			continue
		}
		if arr, ok := AsArray(value); ok {
			for _, entry := range arr {
				if e, ok := AsObject(entry); ok {
					fp := StrField(e, "file_path")
					if fp == "" {
						continue
					}
					if key == "json" && applyDecomposedScoreFilter {
						score, _ := JSONF64(e["score"])
						if score <= decomposedScore() {
							continue
						}
					}
					inputFiles = append(inputFiles, fp)
				}
			}
		} else if e, ok := AsObject(value); ok {
			if fp := StrField(e, "file_path"); fp != "" {
				inputFiles = append(inputFiles, fp)
			}
		}
	}
	return inputFiles
}

// ExtractInputFiles extracts file paths from survivor/pruner data.
func ExtractInputFiles(data any, applyDecomposedScoreFilter bool) []string {
	if obj, ok := AsObject(data); ok {
		return extractFromDict(obj, applyDecomposedScoreFilter)
	}
	if arr, ok := AsArray(data); ok {
		var files []string
		for _, entry := range arr {
			if e, ok := AsObject(entry); ok {
				if fp := StrField(e, "file_path"); fp != "" {
					files = append(files, fp)
				}
			}
		}
		return files
	}
	return nil
}

// ParseJSONInput extracts input files and scores.
func ParseJSONInput(data any, applyDecomposedScoreFilter bool) ([]string, map[string]float64) {
	return ExtractInputFiles(data, applyDecomposedScoreFilter), ExtractScores(data)
}

func filterItems(itemsWithScores [][2]any) []any {
	if len(itemsWithScores) >= 3 {
		allAbove := true
		for i := 0; i < 3; i++ {
			if score, ok := itemsWithScores[i][1].(float64); !ok || score < enumScore() {
				allAbove = false
				break
			}
		}
		if allAbove {
			var out []any
			for _, pair := range itemsWithScores {
				if score, ok := pair[1].(float64); ok && score >= enumScore() {
					out = append(out, pair[0])
				}
			}
			return out
		}
	}
	var out []any
	for i := 0; i < len(itemsWithScores) && i < 3; i++ {
		out = append(out, itemsWithScores[i][0])
	}
	return out
}

// FilterAndSortEnums prunes enum values in a schema using scores.
func FilterAndSortEnums(schema *any, scores map[string]float64, preserveValues map[string]struct{}) {
	switch s := (*schema).(type) {
	case map[string]any:
		keys := make([]string, 0, len(s))
		for k := range s {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, key := range keys {
			if key == "enum" {
				if items, ok := AsArray(s["enum"]); ok {
					var preserved, prunable []any
					for _, item := range items {
						str := ValueToString(item)
						if preserveValues != nil {
							if _, ok := preserveValues[str]; ok {
								preserved = append(preserved, item)
								continue
							}
						}
						prunable = append(prunable, item)
					}
					var itemsWithScores [][2]any
					for _, item := range prunable {
						score := scores[ValueToString(item)]
						itemsWithScores = append(itemsWithScores, [2]any{item, score})
					}
					sort.Slice(itemsWithScores, func(i, j int) bool {
						return itemsWithScores[i][1].(float64) > itemsWithScores[j][1].(float64)
					})
					preserved = append(preserved, filterItems(itemsWithScores)...)
					s["enum"] = preserved
				}
			} else {
				inner := s[key]
				FilterAndSortEnums(&inner, scores, preserveValues)
				s[key] = inner
			}
		}
	case []any:
		for i, item := range s {
			inner := item
			FilterAndSortEnums(&inner, scores, preserveValues)
			s[i] = inner
		}
	}
}

// GroupFiles groups input files by root tool key.
func GroupFiles(inputFiles []string, catalog *DecomposedCatalog) (map[string][]string, map[string]struct{}) {
	groups := make(map[string][]string)
	toolFiles := make(map[string]struct{})
	root := decomposedRoot()
	jExt := jsonExt()

	for _, filePath := range inputFiles {
		key := catalog.ResolveKey(filePath)
		if !catalog.HasJSON(key) {
			fmt.Fprintf(os.Stderr, "Warning: File not found: %s\n", filePath)
			continue
		}
		rel, err := filepath.Rel(root, key)
		if err != nil {
			rel = key
		}
		parts := strings.Split(filepath.ToSlash(rel), "/")
		isTool := len(parts) == 1 && strings.HasSuffix(parts[0], jExt)
		rootTool, ok := GetRootToolKey(key)
		if !ok {
			continue
		}
		if isTool {
			toolFiles[key] = struct{}{}
		}
		groups[rootTool] = append(groups[rootTool], key)
	}
	return groups, toolFiles
}

func toolShellFromRootKey(rootTool string) map[string]any {
	name := strings.TrimSuffix(filepath.Base(rootTool), jsonExt())
	return map[string]any{
		"name": name,
		"inputSchema": map[string]any{
			"type": "object", "properties": map[string]any{},
		},
	}
}

// ProcessGroupsOptions configures enum preservation during retrieve.
type ProcessGroupsOptions struct {
	SystemPreserve       map[string]struct{}
	MCPPreserve          map[string]struct{}
	RequiredByTool       map[string]map[string]struct{}
	PruneOptionalTools   map[string]struct{}
}

// BuildProcessGroupsOptions builds retrieve options from policy context.
func BuildProcessGroupsOptions(ctx *PolicyContext, catalogDict any, store *DecomposedCatalog, preserveValues map[string]struct{}) ProcessGroupsOptions {
	systemPreserve := SystemRequiredEnumValues(catalogDict)
	if len(systemPreserve) == 0 && preserveValues != nil {
		systemPreserve = preserveValues
	}
	mcpPreserve := MCPRequiredEnumValues(catalogDict)
	requiredByTool := RequiredEnumValuesByTool(catalogDict)

	pruneOptionalTools := make(map[string]struct{})
	for key := range store.jsonFiles {
		if rootTool, ok := GetRootToolKey(key); ok {
			toolName := ToolIDFromDecomposedRel(rootTool)
			if EffectivePolicy(ctx, toolName) == PolicyPruneOptional {
				pruneOptionalTools[toolName] = struct{}{}
			}
		}
	}

	opts := ProcessGroupsOptions{RequiredByTool: requiredByTool, PruneOptionalTools: pruneOptionalTools}
	if len(systemPreserve) > 0 {
		opts.SystemPreserve = systemPreserve
	}
	if len(mcpPreserve) > 0 {
		opts.MCPPreserve = mcpPreserve
	}
	return opts
}

// ProcessGroupsOptionsFromFields builds options from optional policy fields.
func ProcessGroupsOptionsFromFields(
	systemPreserve, mcpPreserve []string,
	requiredByTool map[string][]string,
	requiredEnumValuesByTool map[string][]string,
	pruneOptionalTools []string,
) ProcessGroupsOptions {
	byTool := requiredByTool
	if byTool == nil {
		byTool = requiredEnumValuesByTool
	}
	if byTool == nil {
		byTool = map[string][]string{}
	}
	required := make(map[string]map[string]struct{})
	for k, v := range byTool {
		set := make(map[string]struct{})
		for _, item := range v {
			set[item] = struct{}{}
		}
		required[k] = set
	}
	opts := ProcessGroupsOptions{RequiredByTool: required}
	if len(systemPreserve) > 0 {
		opts.SystemPreserve = sliceToSet(systemPreserve)
	}
	if len(mcpPreserve) > 0 {
		opts.MCPPreserve = sliceToSet(mcpPreserve)
	}
	if len(pruneOptionalTools) > 0 {
		opts.PruneOptionalTools = sliceToSet(pruneOptionalTools)
	}
	return opts
}

func sliceToSet(items []string) map[string]struct{} {
	set := make(map[string]struct{}, len(items))
	for _, item := range items {
		set[item] = struct{}{}
	}
	return set
}

// ProcessGroups merges grouped files into recomposed tools.
func ProcessGroups(groups map[string][]string, toolFiles map[string]struct{}, scores map[string]float64, catalog *DecomposedCatalog, opts *ProcessGroupsOptions) []any {
	var tools []any
	for rootTool, files := range groups {
		baseTool, ok := catalog.GetJSON(rootTool)
		if !ok {
			baseTool = toolShellFromRootKey(rootTool)
		} else {
			baseTool = CloneValue(baseTool)
		}

		toolNameInSchema := ""
		if obj, ok := AsObject(baseTool); ok {
			toolNameInSchema = StrField(obj, "name")
		}

		for _, fileKey := range files {
			if _, isTool := toolFiles[fileKey]; isTool {
				continue
			}
			baseTool = DeepMerge(baseTool, ClimbAndMerge(fileKey, catalog))
		}

		stemName := strings.TrimSuffix(filepath.Base(rootTool), jsonExt())
		toolName := stemName
		if obj, ok := AsObject(baseTool); ok {
			if name := StrField(obj, "name"); name != "" {
				toolName = name
			} else if toolNameInSchema != "" {
				toolName = toolNameInSchema
			}
			delete(obj, "id")
			obj["name"] = toolName
			baseTool = obj
		}

		if len(scores) > 0 {
			var enumPreserve map[string]struct{}
			if _, prune := opts.PruneOptionalTools[toolName]; prune {
				if set, ok := opts.RequiredByTool[toolName]; ok {
					enumPreserve = set
				} else if opts.SystemPreserve != nil {
					enumPreserve = opts.SystemPreserve
				} else if opts.MCPPreserve != nil {
					enumPreserve = opts.MCPPreserve
				}
			}
			schema := baseTool
			FilterAndSortEnums(&schema, scores, enumPreserve)
			baseTool = schema
		}
		tools = append(tools, baseTool)
	}
	return tools
}

// RetrieveOptions configures retrieve behavior.
type RetrieveOptions struct {
	ApplyDecomposedScoreFilter bool
	ProcessGroups              ProcessGroupsOptions
}

// RetrieveCore merges survivor chunks into recomposed tool schemas.
func RetrieveCore(data any, store, survivorOverlay *DecomposedCatalog, opts *RetrieveOptions) []any {
	if len(survivorOverlay.jsonFiles) > 0 {
		store.MergeJSONFiles(survivorOverlay)
	}
	inputFiles, scores := ParseJSONInput(data, opts.ApplyDecomposedScoreFilter)
	groups, toolFiles := GroupFiles(inputFiles, store)
	return ProcessGroups(groups, toolFiles, scores, store, &opts.ProcessGroups)
}

// RetrieveTools is the high-level retrieve entry point (alias for RetrieveCore).
func RetrieveTools(data any, store, survivorOverlay *DecomposedCatalog, opts *RetrieveOptions) []any {
	return RetrieveCore(data, store, survivorOverlay, opts)
}

// RemovedChunksOptions configures removed chunk computation.
type RemovedChunksOptions struct {
	ApplyDecomposedScoreFilter bool
}

// ChunkSurvivorKey returns a normalized identity for a catalog chunk entry.
func ChunkSurvivorKey(entry any, section string) (string, bool) {
	obj, ok := AsObject(entry)
	if !ok {
		return "", false
	}
	if fp := StrField(obj, "file_path"); fp != "" {
		if key, ok := ToDecomposedKey(fp); ok {
			return key, true
		}
		return fp, true
	}
	if section == "md" {
		if content, ok := obj["content"].(string); ok {
			return "md:content:" + content, true
		}
	}
	return "", false
}

func survivorKeySets(surviving any, applyDecomposedScoreFilter bool) (map[string]struct{}, map[string]struct{}) {
	jsonKeys := make(map[string]struct{})
	mdKeys := make(map[string]struct{})
	obj, ok := AsObject(surviving)
	if !ok {
		return jsonKeys, mdKeys
	}
	if arr, ok := AsArray(obj["json"]); ok {
		for _, entry := range arr {
			e, ok := AsObject(entry)
			if !ok {
				continue
			}
			if applyDecomposedScoreFilter {
				score, _ := JSONF64(e["score"])
				if score <= decomposedScore() {
					continue
				}
			}
			if key, ok := ChunkSurvivorKey(entry, "json"); ok {
				jsonKeys[key] = struct{}{}
			}
		}
	}
	if arr, ok := AsArray(obj["md"]); ok {
		for _, entry := range arr {
			if key, ok := ChunkSurvivorKey(entry, "md"); ok {
				mdKeys[key] = struct{}{}
			}
		}
	}
	return jsonKeys, mdKeys
}

func removedSection(full any, section string, survivorKeys map[string]struct{}) []any {
	obj, ok := AsObject(full)
	if !ok {
		return nil
	}
	arr, ok := AsArray(obj[section])
	if !ok {
		return nil
	}
	var removed []any
	for _, entry := range arr {
		key, ok := ChunkSurvivorKey(entry, section)
		if ok {
			if _, survived := survivorKeys[key]; survived {
				continue
			}
		}
		removed = append(removed, CloneValue(entry))
	}
	return removed
}

// RemovedChunks returns chunks in full catalog but not in surviving input.
func RemovedChunks(fullCatalog, surviving any, opts *RemovedChunksOptions) map[string]any {
	jsonKeys, mdKeys := survivorKeySets(surviving, opts.ApplyDecomposedScoreFilter)
	return map[string]any{
		"json": removedSection(fullCatalog, "json", jsonKeys),
		"md":   removedSection(fullCatalog, "md", mdKeys),
	}
}

// LoadCatalogFromDir loads a decomposed catalog from a directory.
func LoadCatalogFromDir(dirPath string) (map[string]any, error) {
	root := dirPath
	info, err := os.Stat(root)
	if err != nil || !info.IsDir() {
		return nil, fmt.Errorf("directory not found: %s", dirPath)
	}

	var mdEntries, jsonEntries []any
	files, err := walkDirLight(root)
	if err != nil {
		return nil, err
	}

	jExt := trimDot(jsonExt())
	mExt := trimDot(mdExt())

	for _, path := range files {
		pathStr := filepath.ToSlash(path)
		if _, ok := ToDecomposedKey(pathStr); !ok {
			continue
		}
		suffix := strings.ToLower(strings.TrimPrefix(filepath.Ext(path), "."))
		if strings.EqualFold(suffix, mExt) {
			content, err := os.ReadFile(path)
			if err != nil {
				continue
			}
			mdEntries = append(mdEntries, map[string]any{
				"id": strings.TrimSuffix(filepath.Base(path), mdExt()), "file_path": pathStr,
				"score": 0.0, "start_line": 1, "end_line": 1, "language": "markdown", "content": string(content),
			})
		} else if strings.EqualFold(suffix, jExt) {
			rawText, err := os.ReadFile(path)
			if err != nil {
				return nil, err
			}
			var content any
			if err := json.Unmarshal(rawText, &content); err != nil {
				return nil, err
			}
			lineCount := strings.Count(string(rawText), "\n") + 1
			entryID := content
			if obj, ok := AsObject(content); ok {
				if id, ok := obj["id"]; ok {
					entryID = id
				} else if key, ok := ToDecomposedKey(pathStr); ok {
					entryID = ToolIDFromDecomposedRel(key)
				} else {
					entryID = strings.TrimSuffix(filepath.Base(path), jsonExt())
				}
			}
			jsonEntries = append(jsonEntries, map[string]any{
				"id": entryID, "name": entryID, "file_path": pathStr,
				"score": 0.0, "start_line": 1, "end_line": lineCount, "language": "json", "content": content,
			})
		}
	}

	if len(mdEntries) == 0 && len(jsonEntries) == 0 {
		fmt.Fprintf(os.Stderr, "Warning: No .json or .md files found in %s\n", dirPath)
	}
	return map[string]any{"md": mdEntries, "json": jsonEntries}, nil
}

// LoadCatalog is an alias for LoadCatalogFromDir.
func LoadCatalog(dirPath string) (map[string]any, error) {
	return LoadCatalogFromDir(dirPath)
}

func trimDot(ext string) string {
	return strings.TrimPrefix(ext, ".")
}

func walkDirLight(root string) ([]string, error) {
	var stack []string
	stack = append(stack, root)
	var files []string
	for len(stack) > 0 {
		dir := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		entries, err := os.ReadDir(dir)
		if err != nil {
			return nil, err
		}
		for _, entry := range entries {
			path := filepath.Join(dir, entry.Name())
			if entry.IsDir() {
				stack = append(stack, path)
			} else {
				files = append(files, path)
			}
		}
	}
	return files, nil
}
