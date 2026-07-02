// Smoke test for github.com/qdrddr/clear-your-tools/sdk/go consumed from git tag v0.6.4.
//
// Intended to run outside the clear-your-tools repo (copy this folder anywhere).
package main

import (
	"fmt"
	"log"
	"os"
	"strings"

	cytindexer "github.com/qdrddr/clear-your-tools/sdk/go"
)

func main() {
	libVersion, err := cytindexer.Version()
	if err != nil {
		log.Fatalf("Version(): %v (last error: %q)", err, cytindexer.LastError())
	}

	indexJSON, err := cytindexer.BuildCatalogIndex("[]", "[]")
	if err != nil {
		log.Fatalf("BuildCatalogIndex(): %v (last error: %q)", err, cytindexer.LastError())
	}

	fmt.Println("cyt-indexer Go git smoke OK")
	fmt.Printf("  sdk module version: %s\n", cytindexer.ModuleVersion)
	fmt.Printf("  native lib version: %s\n", libVersion)
	fmt.Printf("  empty catalog index bytes: %d\n", len(indexJSON))
	if !strings.Contains(indexJSON, "tools") {
		log.Fatalf("unexpected index JSON: %s", indexJSON)
	}

	if wd, err := os.Getwd(); err == nil {
		fmt.Printf("  cwd: %s\n", wd)
	}
}
