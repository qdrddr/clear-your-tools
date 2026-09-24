Feature: cyt-mcp catalog resilience
  Guard reload, daemon restart, tiers stats, and hook injection when cyt-mcp catalogs
  are cold or the hook daemon registry was reset.

  Scenario: User-scoped tools/list exposes registered tools when runtime cache is cold
    Given a user-scoped cyt-mcp session with an empty runtime cache
    When tools/list is requested through session workspace middleware
    Then tools/list should fall back to registered MCP tools

  Scenario: Workspace-scoped tools/list serves stubs from a populated runtime cache
    Given a workspace-scoped cyt-mcp session with a populated runtime cache
    When tools/list is requested through session workspace middleware
    Then tools/list should include workspace backend stubs

  Scenario: Registry hydrate restores workspace catalog after in-memory registry loss
    Given a workspace cyt-mcp catalog registered in the hook daemon
    And the in-memory catalog registry was cleared like a CLI cold start
    When catalog registry hydration runs for read
    Then catalog_for_hook should expose the registered workspace tools

  Scenario: Master catalog blocking read survives simulated daemon restart
    Given a workspace cyt-mcp catalog registered in the hook daemon
    And the in-memory catalog registry was cleared like a CLI cold start
    When master catalog is loaded blocking for the workspace
    Then master catalog tool count should meet the resilience scenario minimum

  Scenario: Hook inject includes cyt-mcp tools when registry is populated
    Given a workspace cyt-mcp catalog registered in the hook daemon
    And a cyt-mcp hook config with BM25 pruning enabled
    When hook inject runs for the catalog resilience BM25 prompt
    Then hook stdout should include expected cyt-mcp tool names

  Scenario: Dual-layer hook catalog unions user and workspace tools for tiers stats
    Given dual-layer usr and ws cyt-mcp catalogs registered for the workspace
    And a cyt-mcp hook config with tier tracking enabled
    When cyt tiers stats runs with JSON output for the workspace
    Then tiers stats catalog count should equal usr plus ws tool totals
    And tiers stats scope should default to all

  Scenario: User-scoped tools merge from disk when registry has workspace layer only
    Given a workspace cyt-mcp catalog registered in the hook daemon
    And user-scoped cyt-mcp tools cached on disk
    When master catalog is loaded blocking for the workspace
    Then master catalog should include both workspace and user tool names

  Scenario: Tiers stats verbose troubleshooting shows user and workspace catalog breakdown
    Given dual-layer usr and ws cyt-mcp catalogs registered for the workspace
    When cyt tiers stats runs with verbose text output
    Then troubleshooting should show user and workspace catalog counts
