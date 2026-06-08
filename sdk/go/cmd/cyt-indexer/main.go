package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/qdrddr/clear-your-tools/sdk/go/cytindexer"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}
	switch os.Args[1] {
	case "build":
		if err := runBuild(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
	case "retrieve":
		if err := runRetrieve(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
	case "removed":
		if err := runRemoved(os.Args[2:]); err != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
			os.Exit(1)
		}
	case "-h", "--help", "help":
		printUsage()
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `cyt-indexer — tool schema decomposition and catalog indexing

Usage:
  cyt-indexer build --tools <file> --output <dir>
  cyt-indexer retrieve --catalog <dir> --input <file> --output <file> [options]
  cyt-indexer removed --catalog <dir> --input <file> --output <file> [options]

Retrieve options:
  --config <file>           JSON config with defaults and pruning.per_tool
  --system-policy <policy>  always_include | prune_optional | prune_all
  --mcp-policy <policy>       always_include | prune_optional | prune_all
  --per-tool <file>         Per-tool policy JSON object
  --tool-policy TOOL=POLICY Repeatable per-tool override
  --preserve a,b,c          Enum values to preserve
  --score-filter            Drop json chunks with score <= 0.5
  --removed-output <file>   Write non-surviving chunks

Removed options:
  --full <file>             Full catalog JSON instead of walking --catalog
  --score-filter            Treat low-score survivors as non-surviving
`)
}

func runBuild(args []string) error {
	fs := flag.NewFlagSet("build", flag.ContinueOnError)
	tools := fs.String("tools", "", "tools JSON file")
	output := fs.String("output", "", "output directory")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *tools == "" || *output == "" {
		return fmt.Errorf("--tools and --output are required")
	}
	toolsArr, err := loadToolsArray(*tools)
	if err != nil {
		return err
	}
	index := cytindexer.BuildCatalogFromTools(toolsArr)
	if err := os.MkdirAll(*output, 0o755); err != nil {
		return err
	}
	for rel, content := range index.Files {
		path := filepath.Join(*output, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
			return err
		}
	}
	fmt.Fprintf(os.Stderr, "Wrote %d files to %s\n", len(index.Files), *output)
	return nil
}

func runRetrieve(args []string) error {
	fs := flag.NewFlagSet("retrieve", flag.ContinueOnError)
	catalog := fs.String("catalog", "", "catalog directory")
	input := fs.String("input", "", "survivors JSON")
	output := fs.String("output", "", "output tools JSON")
	config := fs.String("config", "", "policy config JSON")
	systemPolicy := fs.String("system-policy", "", "system tool policy")
	mcpPolicy := fs.String("mcp-policy", "", "MCP tool policy")
	perTool := fs.String("per-tool", "", "per-tool policy JSON")
	preserve := fs.String("preserve", "", "comma-separated enum values to preserve")
	scoreFilter := fs.Bool("score-filter", false, "apply decomposed score filter")
	removedOutput := fs.String("removed-output", "", "write removed chunks JSON")
	var toolPolicies multiFlag
	fs.Var(&toolPolicies, "tool-policy", "per-tool override TOOL=POLICY")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *catalog == "" || *input == "" || *output == "" {
		return fmt.Errorf("--catalog, --input, and --output are required")
	}

	ctx, err := policyContextFromCLI(*config, *systemPolicy, *mcpPolicy, *perTool, toolPolicies)
	if err != nil {
		return err
	}

	catalogDict, err := cytindexer.LoadCatalogFromDir(*catalog)
	if err != nil {
		return err
	}
	store := cytindexer.FromCatalogDict(catalogDict)
	inputRaw, err := os.ReadFile(*input)
	if err != nil {
		return err
	}
	var data any
	if err := json.Unmarshal(inputRaw, &data); err != nil {
		return err
	}
	survivor := cytindexer.FromCatalogDict(data)

	var preserveSet map[string]struct{}
	if *preserve != "" {
		preserveSet = make(map[string]struct{})
		for _, v := range strings.Split(*preserve, ",") {
			v = strings.TrimSpace(v)
			if v != "" {
				preserveSet[v] = struct{}{}
			}
		}
	}

	processOpts := cytindexer.BuildProcessGroupsOptions(&ctx, catalogDict, store, preserveSet)
	opts := &cytindexer.RetrieveOptions{
		ApplyDecomposedScoreFilter: *scoreFilter,
		ProcessGroups:              processOpts,
	}
	tools := cytindexer.RetrieveCore(data, store, survivor, opts)
	if len(tools) == 0 && *scoreFilter {
		fmt.Fprintf(os.Stderr, "Warning: retrieve produced 0 tools; rerank/pruner survivors usually need score filter disabled (omit --score-filter)\n")
	}
	out, err := json.MarshalIndent(tools, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(*output, out, 0o644); err != nil {
		return err
	}
	if *removedOutput != "" {
		removed := cytindexer.RemovedChunks(catalogDict, data, &cytindexer.RemovedChunksOptions{
			ApplyDecomposedScoreFilter: *scoreFilter,
		})
		rb, err := json.MarshalIndent(removed, "", "  ")
		if err != nil {
			return err
		}
		if err := os.WriteFile(*removedOutput, rb, 0o644); err != nil {
			return err
		}
	}
	return nil
}

func runRemoved(args []string) error {
	fs := flag.NewFlagSet("removed", flag.ContinueOnError)
	catalog := fs.String("catalog", "", "catalog directory")
	input := fs.String("input", "", "survivors JSON")
	output := fs.String("output", "", "removed chunks JSON")
	full := fs.String("full", "", "full catalog JSON file")
	scoreFilter := fs.Bool("score-filter", false, "apply decomposed score filter")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *catalog == "" || *input == "" || *output == "" {
		return fmt.Errorf("--catalog, --input, and --output are required")
	}

	var fullCatalog map[string]any
	var err error
	if *full != "" {
		raw, err := os.ReadFile(*full)
		if err != nil {
			return err
		}
		if err := json.Unmarshal(raw, &fullCatalog); err != nil {
			return err
		}
	} else {
		fullCatalog, err = cytindexer.LoadCatalogFromDir(*catalog)
		if err != nil {
			return err
		}
	}

	inputRaw, err := os.ReadFile(*input)
	if err != nil {
		return err
	}
	var surviving any
	if err := json.Unmarshal(inputRaw, &surviving); err != nil {
		return err
	}
	removed := cytindexer.RemovedChunks(fullCatalog, surviving, &cytindexer.RemovedChunksOptions{
		ApplyDecomposedScoreFilter: *scoreFilter,
	})
	out, err := json.MarshalIndent(removed, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(*output, out, 0o644)
}

func loadToolsArray(path string) ([]any, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var toolsVal any
	if err := json.Unmarshal(raw, &toolsVal); err != nil {
		return nil, err
	}
	if arr, ok := cytindexer.AsArray(toolsVal); ok {
		return arr, nil
	}
	if obj, ok := cytindexer.AsObject(toolsVal); ok {
		if arr, ok := cytindexer.AsArray(obj["tools"]); ok {
			return arr, nil
		}
	}
	return nil, fmt.Errorf("expected tools array in input JSON")
}

func policyContextFromCLI(configPath, systemPolicy, mcpPolicy, perToolPath string, toolPolicies []string) (cytindexer.PolicyContext, error) {
	var ctx cytindexer.PolicyContext
	if configPath != "" {
		raw, err := os.ReadFile(configPath)
		if err != nil {
			return ctx, err
		}
		var val any
		if err := json.Unmarshal(raw, &val); err != nil {
			return ctx, err
		}
		ctx = cytindexer.PolicyContextFromValues(val)
	} else {
		ctx = cytindexer.NewPolicyContext()
	}
	if systemPolicy != "" {
		p, ok := cytindexer.ToolPolicyFromString(systemPolicy)
		if !ok {
			return ctx, fmt.Errorf("invalid system policy: %s", systemPolicy)
		}
		ctx.SystemPolicy = p
	}
	if mcpPolicy != "" {
		p, ok := cytindexer.ToolPolicyFromString(mcpPolicy)
		if !ok {
			return ctx, fmt.Errorf("invalid mcp policy: %s", mcpPolicy)
		}
		ctx.MCPPolicy = p
	}
	if perToolPath != "" {
		raw, err := os.ReadFile(perToolPath)
		if err != nil {
			return ctx, err
		}
		var val any
		if err := json.Unmarshal(raw, &val); err != nil {
			return ctx, err
		}
		overrides, err := cytindexer.PerToolPoliciesFromValue(val)
		if err != nil {
			return ctx, err
		}
		cytindexer.ApplyPerToolOverrides(&ctx, overrides)
	}
	cliOverrides := make(map[string]cytindexer.ToolPolicy)
	for _, spec := range toolPolicies {
		toolID, policy, err := cytindexer.ParseToolPolicyPair(spec)
		if err != nil {
			return ctx, err
		}
		cliOverrides[toolID] = policy
	}
	cytindexer.ApplyPerToolOverrides(&ctx, cliOverrides)
	return ctx, nil
}

type multiFlag []string

func (m *multiFlag) String() string { return strings.Join(*m, ",") }
func (m *multiFlag) Set(value string) error {
	*m = append(*m, value)
	return nil
}
