package cytindexer

import (
	"encoding/json"
	"fmt"
	"path/filepath"
	"sort"
	"strings"
)

const (
	alwaysInclude = "always_include"
	pruneOptional = "prune_optional"
	pruneAll      = "prune_all"
)

// ToolPolicy represents a catalog pruning policy.
type ToolPolicy int

const (
	PolicyAlwaysInclude ToolPolicy = iota
	PolicyPruneOptional
	PolicyPruneAll
)

// ToolPolicyStrings returns canonical policy string literals.
func ToolPolicyStrings() []string {
	return []string{alwaysInclude, pruneOptional, pruneAll}
}

// ToolPolicyFromString parses a policy string.
func ToolPolicyFromString(s string) (ToolPolicy, bool) {
	switch s {
	case alwaysInclude:
		return PolicyAlwaysInclude, true
	case pruneOptional:
		return PolicyPruneOptional, true
	case pruneAll:
		return PolicyPruneAll, true
	default:
		return PolicyPruneOptional, false
	}
}

func (p ToolPolicy) String() string {
	switch p {
	case PolicyAlwaysInclude:
		return alwaysInclude
	case PolicyPruneOptional:
		return pruneOptional
	case PolicyPruneAll:
		return pruneAll
	default:
		return pruneOptional
	}
}

// PolicyContext holds system/MCP/per-tool pruning policies.
type PolicyContext struct {
	SystemPolicy ToolPolicy
	MCPPolicy    ToolPolicy
	PerTool      map[string]ToolPolicy
}

// NewPolicyContext returns defaults from runtime configuration.
func NewPolicyContext() PolicyContext {
	sys, _ := ToolPolicyFromString(defaultSystemPolicy())
	mcp, _ := ToolPolicyFromString(defaultMCPPolicy())
	return PolicyContext{SystemPolicy: sys, MCPPolicy: mcp, PerTool: make(map[string]ToolPolicy)}
}

// PolicyContextWithOverrides applies optional overrides on top of defaults.
func PolicyContextWithOverrides(system, mcp *ToolPolicy, perTool map[string]ToolPolicy) PolicyContext {
	ctx := NewPolicyContext()
	if system != nil {
		ctx.SystemPolicy = *system
	}
	if mcp != nil {
		ctx.MCPPolicy = *mcp
	}
	if perTool != nil {
		ctx.PerTool = perTool
	}
	return ctx
}

// PolicyContextFromValues applies config JSON defaults and per-tool policies.
func PolicyContextFromValues(config any) PolicyContext {
	ctx := NewPolicyContext()
	obj, ok := AsObject(config)
	if !ok {
		return ctx
	}
	if defaults, ok := AsObject(obj["defaults"]); ok {
		if s, ok := defaults["system_tool_policy"].(string); ok {
			if p, ok := ToolPolicyFromString(s); ok {
				ctx.SystemPolicy = p
			}
		}
		if m, ok := defaults["mcp_tool_policy"].(string); ok {
			if p, ok := ToolPolicyFromString(m); ok {
				ctx.MCPPolicy = p
			}
		}
	}
	if pruning, ok := AsObject(obj["pruning"]); ok {
		if perTool, ok := AsObject(pruning["per_tool"]); ok {
			for toolID, policy := range perTool {
				if s, ok := policy.(string); ok {
					if p, ok := ToolPolicyFromString(s); ok {
						ctx.PerTool[toolID] = p
					}
				}
			}
		}
	}
	return ctx
}

// ParseToolPolicyPair parses `TOOL=POLICY`.
func ParseToolPolicyPair(s string) (string, ToolPolicy, error) {
	parts := strings.SplitN(s, "=", 2)
	if len(parts) != 2 {
		return "", PolicyPruneOptional, fmt.Errorf("expected TOOL=POLICY, got: %s", s)
	}
	toolID := strings.TrimSpace(parts[0])
	if toolID == "" {
		return "", PolicyPruneOptional, fmt.Errorf("expected TOOL=POLICY, got: %s", s)
	}
	policy, ok := ToolPolicyFromString(strings.TrimSpace(parts[1]))
	if !ok {
		return "", PolicyPruneOptional, fmt.Errorf("invalid policy for %s: %s", toolID, parts[1])
	}
	return toolID, policy, nil
}

// PerToolPoliciesFromValue loads per-tool overrides from a JSON object.
func PerToolPoliciesFromValue(val any) (map[string]ToolPolicy, error) {
	obj, ok := AsObject(val)
	if !ok {
		return nil, fmt.Errorf("per-tool policies must be a JSON object")
	}
	out := make(map[string]ToolPolicy)
	for toolID, policyVal := range obj {
		s, ok := policyVal.(string)
		if !ok {
			return nil, fmt.Errorf("policy for %s must be a string", toolID)
		}
		p, ok := ToolPolicyFromString(s)
		if !ok {
			return nil, fmt.Errorf("invalid policy for %s: %s", toolID, s)
		}
		out[toolID] = p
	}
	return out, nil
}

// ApplyPerToolOverrides merges per-tool overrides (later wins).
func ApplyPerToolOverrides(ctx *PolicyContext, overrides map[string]ToolPolicy) {
	for k, v := range overrides {
		ctx.PerTool[k] = v
	}
}

func propertiesFieldEmpty(schema map[string]any) bool {
	props := schema["properties"]
	if props == nil {
		return true
	}
	if m, ok := AsObject(props); ok {
		return len(m) == 0
	}
	return false
}

// IsNonSystemToolID reports MCP-style tool ids.
func IsNonSystemToolID(toolID string) bool { return strings.HasPrefix(toolID, "mcp__") }

// IsSystemToolID reports built-in (non-MCP) tool ids.
func IsSystemToolID(toolID string) bool { return !IsNonSystemToolID(toolID) }

// ChunkToolID extracts tool id from a catalog chunk entry.
func ChunkToolID(item any) string {
	obj, ok := AsObject(item)
	if !ok {
		return ""
	}
	if id := StrField(obj, "id"); id != "" {
		return id
	}
	return StrField(obj, "name")
}

// EffectivePolicy resolves the policy for a tool id.
func EffectivePolicy(ctx *PolicyContext, toolID string) ToolPolicy {
	if p, ok := ctx.PerTool[toolID]; ok {
		return p
	}
	if IsSystemToolID(toolID) {
		return ctx.SystemPolicy
	}
	return ctx.MCPPolicy
}

// ToolPassThrough reports whether a tool should pass through unchanged.
func ToolPassThrough(ctx *PolicyContext, toolID string) bool {
	return EffectivePolicy(ctx, toolID) == PolicyAlwaysInclude
}

// RootToolIDFromChunk resolves the root tool id for a chunk entry.
func RootToolIDFromChunk(item any) string {
	obj, ok := AsObject(item)
	if !ok {
		return ChunkToolID(item)
	}
	filePath := StrField(obj, "file_path")
	if rootKey, ok := GetRootToolKey(filePath); ok {
		return ToolIDFromDecomposedRel(rootKey)
	}
	return ChunkToolID(item)
}

// RequestPassThrough reports whether all named tools pass through.
func RequestPassThrough(ctx *PolicyContext, tools []any) bool {
	var named []map[string]any
	for _, tool := range tools {
		if obj, ok := AsObject(tool); ok && StrField(obj, "name") != "" {
			named = append(named, obj)
		}
	}
	if len(named) == 0 {
		return true
	}
	for _, obj := range named {
		if !ToolPassThrough(ctx, StrField(obj, "name")) {
			return false
		}
	}
	return true
}

func isNonSystemChunk(item any) bool  { return IsNonSystemToolID(ChunkToolID(item)) }
func isSystemChunk(item any) bool     { return IsSystemToolID(ChunkToolID(item)) }

// IsDecomposedToolRootChunk reports whether item is a decomposed root chunk.
func IsDecomposedToolRootChunk(item any) bool {
	obj, ok := AsObject(item)
	if !ok {
		return false
	}
	filePath := StrField(obj, "file_path")
	if filePath == "" {
		return false
	}
	rootKey, ok1 := GetRootToolKey(filePath)
	decomposedKey, ok2 := ToDecomposedKey(filePath)
	return ok1 && ok2 && rootKey == decomposedKey
}

// IsDecomposedOptionalPropertyChunk reports optional property chunks.
func IsDecomposedOptionalPropertyChunk(item any) bool {
	obj, ok := AsObject(item)
	if !ok {
		return false
	}
	filePath := StrField(obj, "file_path")
	if filePath == "" {
		return false
	}
	decomposedKey, ok1 := ToDecomposedKey(filePath)
	rootKey, ok2 := GetRootToolKey(filePath)
	return ok1 && ok2 && rootKey != decomposedKey
}

// IsSystemRootChunk reports system tool root chunks.
func IsSystemRootChunk(item any) bool { return isSystemChunk(item) && IsDecomposedToolRootChunk(item) }

// IsMCPRootChunk reports MCP tool root chunks.
func IsMCPRootChunk(item any) bool { return isNonSystemChunk(item) && IsDecomposedToolRootChunk(item) }

// IsSystemOptionalChunk reports system optional property chunks.
func IsSystemOptionalChunk(item any) bool {
	return isSystemChunk(item) && IsDecomposedOptionalPropertyChunk(item)
}

// IsMCPOptionalChunk reports MCP optional property chunks.
func IsMCPOptionalChunk(item any) bool {
	return isNonSystemChunk(item) && IsDecomposedOptionalPropertyChunk(item)
}

// NeedsPartition reports whether catalog partitioning is required.
func NeedsPartition(ctx *PolicyContext) bool {
	return ctx.SystemPolicy == PolicyPruneOptional || ctx.MCPPolicy == PolicyPruneOptional
}

// UsesPrunedRecompose reports whether a policy uses pruned recompose.
func UsesPrunedRecompose(policy ToolPolicy) bool {
	return policy == PolicyPruneOptional || policy == PolicyPruneAll
}

// NeedsPrunedRecompose reports whether any policy requires pruned recompose.
func NeedsPrunedRecompose(ctx *PolicyContext) bool {
	return UsesPrunedRecompose(ctx.SystemPolicy) || UsesPrunedRecompose(ctx.MCPPolicy)
}

// SystemToolsPassThrough reports whether all system tools pass through.
func SystemToolsPassThrough(ctx *PolicyContext) bool { return ctx.SystemPolicy == PolicyAlwaysInclude }

// MCPToolsPassThrough reports whether all MCP tools pass through.
func MCPToolsPassThrough(ctx *PolicyContext) bool { return ctx.MCPPolicy == PolicyAlwaysInclude }

// FullPassThrough reports whether all tools pass through.
func FullPassThrough(ctx *PolicyContext) bool {
	return ctx.SystemPolicy == PolicyAlwaysInclude && ctx.MCPPolicy == PolicyAlwaysInclude
}

func collectEnumValuesFromChunks(chunks []any) map[string]struct{} {
	values := make(map[string]struct{})
	for _, item := range chunks {
		if obj, ok := AsObject(item); ok {
			for _, val := range CollectEnums(obj["content"]) {
				values[ValueToString(val)] = struct{}{}
			}
		}
	}
	return values
}

func enumMDMatchesValues(mdItem any, enumValues map[string]struct{}) bool {
	if len(enumValues) == 0 {
		return false
	}
	obj, ok := AsObject(mdItem)
	if !ok {
		return false
	}
	content := ValueToString(obj["content"])
	_, ok = enumValues[content]
	return ok
}

func shouldPinJSONChunk(ctx *PolicyContext, item any) bool {
	if !IsDecomposedToolRootChunk(item) {
		return false
	}
	return EffectivePolicy(ctx, RootToolIDFromChunk(item)) == PolicyPruneOptional
}

// CatalogNeedsPartition inspects catalog data for partition requirements.
func CatalogNeedsPartition(data any, ctx *PolicyContext) bool {
	if NeedsPartition(ctx) {
		return true
	}
	obj, ok := AsObject(data)
	if !ok {
		return false
	}
	jsonItems, ok := AsArray(obj["json"])
	if !ok {
		return false
	}
	seen := make(map[string]struct{})
	for _, item := range jsonItems {
		if _, ok := AsObject(item); !ok {
			continue
		}
		toolID := RootToolIDFromChunk(item)
		if _, dup := seen[toolID]; dup {
			continue
		}
		seen[toolID] = struct{}{}
		if EffectivePolicy(ctx, toolID) == PolicyPruneOptional {
			return true
		}
	}
	return false
}

// CatalogNeedsPrunedRecompose inspects catalog data for pruned recompose requirements.
func CatalogNeedsPrunedRecompose(data any, ctx *PolicyContext) bool {
	if NeedsPrunedRecompose(ctx) {
		return true
	}
	obj, ok := AsObject(data)
	if !ok {
		return false
	}
	jsonItems, ok := AsArray(obj["json"])
	if !ok {
		return false
	}
	seen := make(map[string]struct{})
	for _, item := range jsonItems {
		if _, ok := AsObject(item); !ok {
			continue
		}
		toolID := RootToolIDFromChunk(item)
		if _, dup := seen[toolID]; dup {
			continue
		}
		seen[toolID] = struct{}{}
		if UsesPrunedRecompose(EffectivePolicy(ctx, toolID)) {
			return true
		}
	}
	return false
}

func partitionJSONItems(ctx *PolicyContext, jsonList []any) ([]any, []any, map[string]struct{}, map[string]struct{}, map[string]map[string]struct{}) {
	var pinnedJSON, processableJSON []any
	systemRequired := make(map[string]struct{})
	mcpRequired := make(map[string]struct{})
	requiredByTool := make(map[string]map[string]struct{})

	for _, item := range jsonList {
		if _, ok := AsObject(item); !ok {
			continue
		}
		if shouldPinJSONChunk(ctx, item) {
			copyItem := CloneValue(item)
			pinnedJSON = append(pinnedJSON, copyItem)
			toolID := RootToolIDFromChunk(item)
			enumVals := collectEnumValuesFromChunks([]any{copyItem})
			if requiredByTool[toolID] == nil {
				requiredByTool[toolID] = make(map[string]struct{})
			}
			for v := range enumVals {
				requiredByTool[toolID][v] = struct{}{}
				if isSystemChunk(item) {
					systemRequired[v] = struct{}{}
				} else if isNonSystemChunk(item) {
					mcpRequired[v] = struct{}{}
				}
			}
		} else {
			processableJSON = append(processableJSON, item)
		}
	}
	return pinnedJSON, processableJSON, systemRequired, mcpRequired, requiredByTool
}

func partitionMDItems(mdList []any, pinnedEnumValues map[string]struct{}) ([]any, []any) {
	var processableMD, pinnedMD []any
	for _, mdItem := range mdList {
		if _, ok := AsObject(mdItem); !ok {
			continue
		}
		copyItem := CloneValue(mdItem)
		if enumMDMatchesValues(copyItem, pinnedEnumValues) {
			pinnedMD = append(pinnedMD, copyItem)
		} else {
			processableMD = append(processableMD, copyItem)
		}
	}
	return processableMD, pinnedMD
}

// PartitionCatalog splits catalog into processable and pinned sections.
func PartitionCatalog(data any, ctx *PolicyContext) (map[string]any, map[string]any) {
	if !CatalogNeedsPartition(data, ctx) {
		if obj, ok := AsObject(data); ok {
			return CloneValue(obj).(map[string]any), map[string]any{}
		}
		return map[string]any{}, map[string]any{}
	}
	obj, _ := AsObject(data)
	var jsonList, mdList []any
	if arr, ok := AsArray(obj["json"]); ok {
		jsonList = arr
	}
	if arr, ok := AsArray(obj["md"]); ok {
		mdList = arr
	}

	processable := make(map[string]any)
	for k, v := range obj {
		switch k {
		case "json", "md", "system_required_enum_values", "mcp_required_enum_values", "required_enum_values_by_tool":
		default:
			processable[k] = CloneValue(v)
		}
	}
	pinned := map[string]any{
		"json": []any{}, "md": []any{},
		"system_required_enum_values": []any{}, "mcp_required_enum_values": []any{},
		"required_enum_values_by_tool": map[string]any{},
	}

	pinnedJSON, processableJSON, systemRequired, mcpRequired, requiredByTool := partitionJSONItems(ctx, jsonList)
	pinnedEnumValues := make(map[string]struct{})
	for _, vals := range requiredByTool {
		for v := range vals {
			pinnedEnumValues[v] = struct{}{}
		}
	}
	processableMD, pinnedMD := partitionMDItems(mdList, pinnedEnumValues)

	processable["json"] = processableJSON
	processable["md"] = processableMD
	pinned["json"] = pinnedJSON
	pinned["md"] = pinnedMD

	pinned["system_required_enum_values"] = sortedStringArray(systemRequired)
	pinned["mcp_required_enum_values"] = sortedStringArray(mcpRequired)

	byTool := make(map[string]any)
	for toolID, vals := range requiredByTool {
		byTool[toolID] = sortedStringArray(vals)
	}
	pinned["required_enum_values_by_tool"] = byTool

	return processable, pinned
}

func sortedStringArray(set map[string]struct{}) []any {
	vals := make([]string, 0, len(set))
	for v := range set {
		vals = append(vals, v)
	}
	sort.Strings(vals)
	out := make([]any, len(vals))
	for i, v := range vals {
		out[i] = v
	}
	return out
}

// MergeCatalog merges processed and pinned catalog sections.
func MergeCatalog(processed, pinned map[string]any) map[string]any {
	merged := CloneValue(processed).(map[string]any)
	if pinnedJSON, ok := AsArray(pinned["json"]); ok {
		if arr, ok := AsArray(merged["json"]); ok {
			merged["json"] = append(arr, pinnedJSON...)
		} else {
			merged["json"] = pinnedJSON
		}
	}
	if pinnedMD, ok := AsArray(pinned["md"]); ok {
		if arr, ok := AsArray(merged["md"]); ok {
			merged["md"] = append(arr, pinnedMD...)
		} else {
			merged["md"] = pinnedMD
		}
	}
	for _, key := range []string{"system_required_enum_values", "mcp_required_enum_values", "required_enum_values_by_tool"} {
		if v, ok := pinned[key]; ok {
			merged[key] = CloneValue(v)
		}
	}
	return merged
}

// StashSystemTools filters system tools from a tool list.
func StashSystemTools(tools []any) []any {
	var out []any
	for _, tool := range tools {
		if obj, ok := AsObject(tool); ok && IsSystemToolID(StrField(obj, "name")) {
			out = append(out, CloneValue(tool))
		}
	}
	return out
}

// RestoreSystemTools returns stashed system tools.
func RestoreSystemTools(stash []any) []any { return CloneSlice(stash) }

// StashMCPTools filters MCP tools from a tool list.
func StashMCPTools(tools []any) []any {
	var out []any
	for _, tool := range tools {
		if obj, ok := AsObject(tool); ok && IsNonSystemToolID(StrField(obj, "name")) {
			out = append(out, CloneValue(tool))
		}
	}
	return out
}

// RestoreMCPTools returns stashed MCP tools.
func RestoreMCPTools(stash []any) []any { return CloneSlice(stash) }

// MergeToolsPreservingOrder merges pruned and stashed tools in original order.
func MergeToolsPreservingOrder(original []any, prunedByName, stashedByName map[string]any) []any {
	var result []any
	for _, tool := range original {
		obj, ok := AsObject(tool)
		if !ok {
			continue
		}
		name := StrField(obj, "name")
		if name == "" {
			continue
		}
		if t, ok := stashedByName[name]; ok {
			result = append(result, t)
		} else if t, ok := prunedByName[name]; ok {
			result = append(result, t)
		}
	}
	return result
}

// AnthropicToolIsSystem reports whether an Anthropic tool is a system tool.
func AnthropicToolIsSystem(tool any) bool {
	obj, ok := AsObject(tool)
	return ok && IsSystemToolID(StrField(obj, "name"))
}

// AnthropicToolIsMCP reports whether an Anthropic tool is an MCP tool.
func AnthropicToolIsMCP(tool any) bool {
	obj, ok := AsObject(tool)
	return ok && IsNonSystemToolID(StrField(obj, "name"))
}

// SplitAnthropicTools splits tools into non-system and system groups.
func SplitAnthropicTools(tools []any) (nonSystem, system []any) {
	for _, tool := range tools {
		if AnthropicToolIsSystem(tool) {
			system = append(system, CloneValue(tool))
		} else {
			nonSystem = append(nonSystem, CloneValue(tool))
		}
	}
	return
}

// EntriesForPolicy filters catalog entries by pass-through policy.
func EntriesForPolicy(ctx *PolicyContext, allEntries []any) []any {
	var result []any
	for _, entry := range allEntries {
		toolID := ""
		if obj, ok := AsObject(entry); ok {
			toolID = StrField(obj, "id")
		}
		if toolID != "" && ToolPassThrough(ctx, toolID) {
			continue
		}
		result = append(result, CloneValue(entry))
	}
	return result
}

// ToolsForCatalog filters tools by pass-through policy.
func ToolsForCatalog(ctx *PolicyContext, tools []any) []any {
	var result []any
	for _, tool := range tools {
		name := ""
		if obj, ok := AsObject(tool); ok {
			name = StrField(obj, "name")
		}
		if name != "" && ToolPassThrough(ctx, name) {
			continue
		}
		result = append(result, CloneValue(tool))
	}
	return result
}

// SystemRequiredEnumValues reads pinned system enum values from catalog data.
func SystemRequiredEnumValues(data any) map[string]struct{} {
	return enumValuesFromKey(data, "system_required_enum_values")
}

// MCPRequiredEnumValues reads pinned MCP enum values from catalog data.
func MCPRequiredEnumValues(data any) map[string]struct{} {
	return enumValuesFromKey(data, "mcp_required_enum_values")
}

func enumValuesFromKey(data any, key string) map[string]struct{} {
	obj, ok := AsObject(data)
	if !ok {
		return nil
	}
	arr, ok := AsArray(obj[key])
	if !ok {
		return nil
	}
	out := make(map[string]struct{})
	for _, v := range arr {
		out[ValueToString(v)] = struct{}{}
	}
	return out
}

// RequiredEnumValuesByTool reads per-tool required enum values.
func RequiredEnumValuesByTool(data any) map[string]map[string]struct{} {
	obj, ok := AsObject(data)
	if !ok {
		return nil
	}
	raw, ok := AsObject(obj["required_enum_values_by_tool"])
	if !ok {
		return nil
	}
	out := make(map[string]map[string]struct{})
	for toolID, values := range raw {
		arr, ok := AsArray(values)
		if !ok {
			continue
		}
		set := make(map[string]struct{})
		for _, v := range arr {
			set[ValueToString(v)] = struct{}{}
		}
		out[toolID] = set
	}
	return out
}

// OptionalLeafSurvivedRerank reports whether an optional leaf survived rerank/LLM selection.
func OptionalLeafSurvivedRerank(ctx *PolicyContext, item any, rerankScoreVal float64, llmSelectedPaths map[string]struct{}) bool {
	if !IsDecomposedOptionalPropertyChunk(item) {
		return false
	}
	obj, _ := AsObject(item)
	filePath := StrField(obj, "file_path")
	if llmSelectedPaths != nil {
		if _, ok := llmSelectedPaths[filePath]; ok {
			return true
		}
	}
	policy := EffectivePolicy(ctx, RootToolIDFromChunk(item))
	switch policy {
	case PolicyPruneAll:
		return true
	case PolicyPruneOptional:
		score, _ := JSONF64(obj["score"])
		return score >= rerankScoreVal
	default:
		return false
	}
}

// FilterRecomposeJSONEntries filters json entries for pruned recompose.
func FilterRecomposeJSONEntries(ctx *PolicyContext, jsonList []any, rerankScoreVal float64, llmSelectedPaths map[string]struct{}) []any {
	var filtered []any
	for _, item := range jsonList {
		if IsDecomposedToolRootChunk(item) {
			filtered = append(filtered, CloneValue(item))
		} else if OptionalLeafSurvivedRerank(ctx, item, rerankScoreVal, llmSelectedPaths) {
			filtered = append(filtered, CloneValue(item))
		}
	}
	return filtered
}

// IsDirectRootOptionalPropertyChunk reports direct-child optional property chunks.
func IsDirectRootOptionalPropertyChunk(item any) bool {
	if !IsDecomposedOptionalPropertyChunk(item) {
		return false
	}
	obj, _ := AsObject(item)
	filePath := StrField(obj, "file_path")
	key, ok := ToDecomposedKey(filePath)
	if !ok {
		return false
	}
	rel, err := filepath.Rel(decomposedRoot(), key)
	if err != nil {
		return false
	}
	parts := strings.Split(filepath.ToSlash(rel), "/")
	return len(parts) == 2 && strings.HasSuffix(parts[1], jsonExt())
}

func chunkInputSchema(item any) map[string]any {
	obj, ok := AsObject(item)
	if !ok {
		return nil
	}
	content, ok := AsObject(obj["content"])
	if !ok {
		return nil
	}
	if schema, ok := AsObject(content["inputSchema"]); ok {
		return schema
	}
	if schema, ok := AsObject(content["input_schema"]); ok {
		return schema
	}
	return nil
}

// RootChunkPropertiesEmpty reports whether a root chunk has empty properties.
func RootChunkPropertiesEmpty(item any) bool {
	if !IsDecomposedToolRootChunk(item) {
		return false
	}
	schema := chunkInputSchema(item)
	return schema == nil || propertiesFieldEmpty(schema)
}

// ToolIDHasEmptyDecomposedRoot checks the on-disk decomposed root for empty properties.
func ToolIDHasEmptyDecomposedRoot(catalogIndex *CatalogIndex, toolID string) bool {
	rel := decomposedPrefix() + toolID + jsonExt()
	raw, ok := catalogIndex.Files[rel]
	if !ok {
		return false
	}
	var parsed any
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return false
	}
	obj, ok := AsObject(parsed)
	if !ok {
		return true
	}
	schema, ok := AsObject(obj["inputSchema"])
	if !ok {
		if schema, ok = AsObject(obj["input_schema"]); !ok {
			return true
		}
	}
	return propertiesFieldEmpty(schema)
}

func originalToolInputSchema(catalogIndex *CatalogIndex, toolID string) map[string]any {
	fullRel := "schemas/full/" + toolID + jsonExt()
	if raw, ok := catalogIndex.Files[fullRel]; ok {
		var parsed any
		if json.Unmarshal([]byte(raw), &parsed) == nil {
			if obj, ok := AsObject(parsed); ok {
				if schema, ok := AsObject(obj["inputSchema"]); ok {
					return schema
				}
				if schema, ok := AsObject(obj["input_schema"]); ok {
					return schema
				}
			}
		}
	}
	for _, entry := range catalogIndex.Tools {
		if obj, ok := AsObject(entry); ok && StrField(obj, "id") == toolID {
			if fs, ok := AsObject(obj["full_schema"]); ok {
				if schema, ok := AsObject(fs["inputSchema"]); ok {
					return schema
				}
				if schema, ok := AsObject(fs["input_schema"]); ok {
					return schema
				}
			}
		}
	}
	return nil
}

// ToolIDHadEmptyOriginalRootProperties checks whether the original tool had empty root properties.
func ToolIDHadEmptyOriginalRootProperties(catalogIndex *CatalogIndex, toolID string) bool {
	return propertiesFieldEmpty(originalToolInputSchema(catalogIndex, toolID))
}

// NeedsEmptyOptionalMitigation reports whether empty optional mitigation is needed.
func NeedsEmptyOptionalMitigation(catalogIndex *CatalogIndex, toolID string) bool {
	return ToolIDHasEmptyDecomposedRoot(catalogIndex, toolID) && !ToolIDHadEmptyOriginalRootProperties(catalogIndex, toolID)
}

// OptionalChunksForTool returns optional property chunks for a tool.
func OptionalChunksForTool(items []any, toolID string) []any {
	var out []any
	for _, item := range items {
		if _, ok := AsObject(item); ok && IsDecomposedOptionalPropertyChunk(item) && RootToolIDFromChunk(item) == toolID {
			out = append(out, CloneValue(item))
		}
	}
	return out
}

// DirectRootOptionalChunksForTool returns direct-child optional chunks for a tool.
func DirectRootOptionalChunksForTool(items []any, toolID string) []any {
	var out []any
	for _, item := range OptionalChunksForTool(items, toolID) {
		if IsDirectRootOptionalPropertyChunk(item) {
			out = append(out, item)
		}
	}
	return out
}

// MitigateEmptyOptionalProperties adds fallback optional chunks for empty roots.
func MitigateEmptyOptionalProperties(ctx *PolicyContext, entries []any, catalogIndex *CatalogIndex, postRerankScored any, pipeline []string) []any {
	if len(pipeline) == 0 || len(entries) == 0 {
		return CloneSlice(entries)
	}
	lastStage := pipeline[len(pipeline)-1]
	if lastStage != "rerank" && lastStage != "llm" && lastStage != "bm25" {
		return CloneSlice(entries)
	}

	rootsByTool := make(map[string]any)
	for _, item := range entries {
		if _, ok := AsObject(item); ok && IsDecomposedToolRootChunk(item) {
			rootsByTool[RootToolIDFromChunk(item)] = item
		}
	}
	if len(rootsByTool) == 0 {
		return CloneSlice(entries)
	}

	scoredJSON := scoredJSONEntries(postRerankScored)
	result := CloneSlice(entries)
	seenPaths := make(map[string]struct{})
	for _, item := range result {
		if obj, ok := AsObject(item); ok {
			seenPaths[StrField(obj, "file_path")] = struct{}{}
		}
	}
	toolsToDrop := make(map[string]struct{})

	for toolID, rootItem := range rootsByTool {
		if !shouldMitigateEmptyRoot(ctx, toolID, rootItem, result, catalogIndex) {
			continue
		}
		if lastStage == "llm" {
			toolsToDrop[toolID] = struct{}{}
			continue
		}
		if (lastStage == "rerank" || lastStage == "bm25") && len(scoredJSON) > 0 {
			appendRerankFallbackChunks(toolID, &result, seenPaths, scoredJSON)
		}
	}
	return dropToolsFromEntries(result, toolsToDrop)
}

func scoredJSONEntries(postRerankScored any) []any {
	obj, ok := AsObject(postRerankScored)
	if !ok {
		return nil
	}
	arr, ok := AsArray(obj["json"])
	if !ok {
		return nil
	}
	var out []any
	for _, item := range arr {
		if _, ok := AsObject(item); ok {
			out = append(out, item)
		}
	}
	return out
}

func shouldMitigateEmptyRoot(ctx *PolicyContext, toolID string, rootItem any, entries []any, catalogIndex *CatalogIndex) bool {
	if !UsesPrunedRecompose(EffectivePolicy(ctx, toolID)) {
		return false
	}
	if !NeedsEmptyOptionalMitigation(catalogIndex, toolID) {
		return false
	}
	if !RootChunkPropertiesEmpty(rootItem) {
		return false
	}
	return len(OptionalChunksForTool(entries, toolID)) == 0
}

func appendRerankFallbackChunks(toolID string, result *[]any, seenPaths map[string]struct{}, scoredJSON []any) {
	candidates := OptionalChunksForTool(scoredJSON, toolID)
	sort.Slice(candidates, func(i, j int) bool {
		oi, _ := AsObject(candidates[i])
		oj, _ := AsObject(candidates[j])
		si, _ := JSONF64(oi["score"])
		sj, _ := JSONF64(oj["score"])
		return sj > si
	})
	k := emptyOptionalFallbackK()
	for i, chunk := range candidates {
		if i >= k {
			break
		}
		obj, _ := AsObject(chunk)
		filePath := StrField(obj, "file_path")
		if filePath == "" {
			continue
		}
		if _, seen := seenPaths[filePath]; seen {
			continue
		}
		seenPaths[filePath] = struct{}{}
		*result = append(*result, CloneValue(chunk))
	}
}

func dropToolsFromEntries(entries []any, toolsToDrop map[string]struct{}) []any {
	if len(toolsToDrop) == 0 {
		return entries
	}
	var out []any
	for _, item := range entries {
		if _, ok := AsObject(item); ok {
			if _, drop := toolsToDrop[RootToolIDFromChunk(item)]; !drop {
				out = append(out, item)
			}
		}
	}
	return out
}

// DropRecomposedToolsWithEmptyProperties drops recomposed tools with empty properties when mitigated.
func DropRecomposedToolsWithEmptyProperties(ctx *PolicyContext, tools []any, catalogIndex *CatalogIndex) []any {
	var kept []any
	for _, tool := range tools {
		obj, ok := AsObject(tool)
		if !ok {
			continue
		}
		name := StrField(obj, "name")
		var schema map[string]any
		if s, ok := AsObject(obj["inputSchema"]); ok {
			schema = s
		} else if s, ok := AsObject(obj["input_schema"]); ok {
			schema = s
		}
		hasProps := schema != nil && !propertiesFieldEmpty(schema)
		if hasProps {
			kept = append(kept, CloneValue(tool))
			continue
		}
		if name != "" && UsesPrunedRecompose(EffectivePolicy(ctx, name)) && NeedsEmptyOptionalMitigation(catalogIndex, name) {
			continue
		}
		kept = append(kept, CloneValue(tool))
	}
	return kept
}

func CloneSlice(items []any) []any {
	out := make([]any, len(items))
	for i, item := range items {
		out[i] = CloneValue(item)
	}
	return out
}

