Feature: cyt-mcp get-tool-definitions tool
  On-demand full backend tool definitions via get-tool-definitions.

  Scenario: get-tool-definitions returns full definition for a cached backend tool
    Given a runtime cache with tool codebase-memory-mcp_search_graph and full inputSchema
    And a search index entry for codebase-memory-mcp_search_graph with outputSchema
    When get-tool-definitions is called with tool_name codebase-memory-mcp_search_graph
    Then search result should include full inputSchema for codebase-memory-mcp_search_graph
    And search result should include outputSchema annotations and meta when indexed

  Scenario: get-tool-definitions rejects self-lookup on nested tool_name
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When get-tool-definitions is called with tool_name cyt-mcp_get-tool-definitions
    Then search should fail with self-lookup error

  Scenario: get-tool-definitions rejects unrelated tool_name below BM25 threshold
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When get-tool-definitions is called with tool_name totally-unrelated-xyz-tool-name
    Then search should fail with unknown tool error

  Scenario: get-tool-definitions fuzzy-matches highest BM25 score above threshold
    Given a runtime cache with tools codebase-memory-mcp_search_graph and codebase-memory-mcp_query_graph
    When get-tool-definitions is called with tool_name codebase-memory-mcp_search_grph
    Then search result should resolve tool_name to codebase-memory-mcp_search_graph

  Scenario: Catalog export excludes get-tool-definitions and never seeds it
    Given a runtime cache with only backend tool codebase-memory-mcp_query_graph
    When catalog payload is exported for agent cursor
    Then catalog tool names should not include get-tool-definitions wire name
    And catalog tool names should include codebase-memory-mcp_query_graph

  Scenario: get-tool-definitions stub is never pruned like backend stubs
    Given get-tool-definitions tool and backend tool codebase-memory-mcp_search_graph
    When stub list transform runs for agent cursor without descriptions
    Then get-tool-definitions stub should retain tool_name enum schema and description
    And backend stub should have minimal empty inputSchema
    And get-tool-definitions should not be stored in runtime catalog cache

  Scenario: cyt-mcp search CLI prints JSON with --json
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When cyt-mcp search CLI is run with --json for codebase-memory-mcp_search_graph
    Then CLI stdout should be valid JSON with full inputSchema
