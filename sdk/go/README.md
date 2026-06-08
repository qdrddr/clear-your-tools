# cyt-indexer (Go)

Go library and CLI for tool schema decomposition and catalog indexing ([Clear Your Tools](https://github.com/qdrddr/clear-your-tools)).

This package mirrors the Rust [`cyt-indexer`](../rust/cyt-indexer) SDK: build decomposed catalogs from Anthropic API tools, retrieve merged schemas from pruner/rerank survivors, and apply system/MCP pruning policies.

## Import

Add the module to your project:

```bash
go get github.com/qdrddr/clear-your-tools/sdk/go/cytindexer
```

In application code:

```go
import "github.com/qdrddr/clear-your-tools/sdk/go/cytindexer"
```

If you depend on a local checkout (before publishing), add a `replace` directive in your `go.mod`:

```go
replace github.com/qdrddr/clear-your-tools/sdk/go => /path/to/clear-your-tools/sdk/go
```

## Library

Build a decomposed catalog from Anthropic tools or pre-built catalog entries:

```go
package main

import (
    "encoding/json"
    "fmt"

    "github.com/qdrddr/clear-your-tools/sdk/go/cytindexer"
)

func main() {
    tools := []any{
        map[string]any{
            "name":        "Agent",
            "description": "Launch agents",
            "input_schema": map[string]any{
                "type": "object",
                "properties": map[string]any{
                    "prompt": map[string]any{"type": "string"},
                },
                "required": []any{"prompt"},
            },
        },
    }

    index := cytindexer.BuildCatalogFromTools(tools)
    fmt.Println(len(index.Files), "files")

    // Optional: write to disk
    _ = cytindexer.WriteCatalogIndex(&index, ".catalog", true)
}
```

Retrieve merged tool schemas from survivor JSON:

```go
full, _ := cytindexer.LoadCatalog(".catalog")
survivors := map[string]any{ /* rerank / pruner output */ }

store := cytindexer.FromCatalogDict(full)
overlay := cytindexer.FromCatalogDict(survivors)
ctx := cytindexer.NewPolicyContext()
opts := &cytindexer.RetrieveOptions{
    ProcessGroups: cytindexer.BuildProcessGroupsOptions(&ctx, full, store, nil),
}
tools := cytindexer.RetrieveTools(survivors, store, overlay, opts)

removed := cytindexer.RemovedChunks(full, survivors, &cytindexer.RemovedChunksOptions{})
_ = tools
_ = removed
```

### Public API surface

The Go package exports the same core types and functions as the Rust crate:

| Area | Key symbols |
|------|-------------|
| Build | `BuildCatalogIndex`, `BuildCatalogFromTools`, `CatalogIndex`, `DecomposeToolSchema`, `CatalogToolCount` |
| Tools | `AnthropicToolToCatalogEntry`, `PrepareToolEntry`, `TruncateDescription` |
| Catalog I/O | `WriteCatalogIndex`, `CatalogBuilder`, `LoadCatalog` |
| Retrieve | `RetrieveTools`, `DecomposedCatalog`, `RemovedChunks`, `DeepMerge`, `ClimbAndMerge` |
| Policies | `PolicyContext`, `ToolPolicy`, `PartitionCatalog`, `EffectivePolicy`, … |
| Paths / runtime | `ConfigurePaths`, `ConfigureRuntime`, `CollectEnums`, `ToDecomposedKey` |
| Documents | `ExtractDocumentText`, `ExtractLevelInfo` |

## CLI

Install from the repo:

```bash
cd sdk/go
go install ./cmd/cyt-indexer
```

Or build a binary:

```bash
go build -o cyt-indexer ./cmd/cyt-indexer
```

### Commands

Accepts Anthropic API tools (`name`, `description`, `input_schema`) or pre-built catalog entries (`id`, `full_schema`).

```bash
jq '.body.tools' debug/full_example.json > /tmp/tools.json
cyt-indexer build --tools /tmp/tools.json --output ./.catalog

cyt-indexer retrieve --catalog ./.catalog --input survivors.json --output out.json \
  --config config.json \
  --system-policy prune_optional \
  --mcp-policy prune_all \
  --tool-policy Agent=always_include \
  --tool-policy mcp__fff__multi_grep=always_include \
  --per-tool per-tool.json

# Optional: write non-surviving decomposed chunks
cyt-indexer retrieve ... --removed-output ./.catalog/removed.json

# Or as a dedicated command
cyt-indexer removed --catalog ./.catalog --input survivors.json --output ./.catalog/removed.json
```

Score filter is **off by default** (rerank survivor scores are ~0.003, not 0–1). Use `--score-filter` only for LLM-stage catalogs where json scores exceed the decomposed threshold (0.5).

### Policies (precedence order, later wins)

1. `--config` → `defaults.system_tool_policy`, `defaults.mcp_tool_policy`, `pruning.per_tool`
2. `--system-policy` / `--mcp-policy` CLI overrides
3. `--per-tool` JSON file: `{"Agent": "always_include", "Bash": "prune_optional"}`
4. `--tool-policy TOOL=POLICY` (repeatable)

Valid policies: `always_include`, `prune_optional`, `prune_all`.

## Development

```bash
cd sdk/go
go test ./...
go build ./...
```

## License

Apache-2.0 — see [LICENSE](LICENSE).
