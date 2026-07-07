package cytindexer

import "encoding/json"

// ToolKind overrides system vs MCP classification for all tools in a prune session.
type ToolKind string

const (
	ToolKindSystem ToolKind = "system"
	ToolKindMcp    ToolKind = "mcp"
)

// PolicyContext is the JSON shape accepted by cyt_* functions that take ctx_json.
//
// Fields mirror the Python/TypeScript PolicyContext bindings. tool_kind is a runtime-only
// batch override (not read from YAML config); when set to ToolKindMcp, bare executor-style
// tool ids are classified as MCP instead of requiring an mcp__ name prefix.
type PolicyContext struct {
	SystemPolicy string            `json:"system_policy,omitempty"`
	McpPolicy    string            `json:"mcp_policy,omitempty"`
	PerTool      map[string]string `json:"per_tool,omitempty"`
	ToolKind     *ToolKind         `json:"tool_kind,omitempty"`
}

// MarshalJSONString serializes ctx for cyt_* ctx_json parameters.
func (ctx PolicyContext) MarshalJSONString() (string, error) {
	b, err := json.Marshal(ctx)
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// ApplyToolKind sets the batch tool-kind override on ctx.
func ApplyToolKind(ctx *PolicyContext, kind ToolKind) {
	ctx.ToolKind = &kind
}

// ScoringPolicyContext maps description policy variants to base scoring policies and
// copies tool_kind (mirrors Python scoring_policy_context).
func ScoringPolicyContext(ctx PolicyContext) (PolicyContext, error) {
	scoring := PolicyContext{
		PerTool:  make(map[string]string, len(ctx.PerTool)),
		ToolKind: ctx.ToolKind,
	}
	var err error
	if ctx.SystemPolicy != "" {
		scoring.SystemPolicy, err = ScoringPolicy(ctx.SystemPolicy)
		if err != nil {
			return PolicyContext{}, err
		}
	}
	if ctx.McpPolicy != "" {
		scoring.McpPolicy, err = ScoringPolicy(ctx.McpPolicy)
		if err != nil {
			return PolicyContext{}, err
		}
	}
	for toolID, policy := range ctx.PerTool {
		scoring.PerTool[toolID], err = ScoringPolicy(policy)
		if err != nil {
			return PolicyContext{}, err
		}
	}
	return scoring, nil
}
