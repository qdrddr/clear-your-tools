Feature: cyt-mcp search tool
  On-demand full backend tool definitions via cyt-mcp_search.

  Scenario: Search returns full definition for a cached backend tool
    Given a runtime cache with tool codebase-memory-mcp_search_graph and full inputSchema
    And a search index entry for codebase-memory-mcp_search_graph with outputSchema
    When cyt-mcp_search is called with tool_name codebase-memory-mcp_search_graph
    Then search result should include full inputSchema for codebase-memory-mcp_search_graph
    And search result should include outputSchema annotations and meta when indexed

  Scenario: Search rejects self-lookup on nested tool_name
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When cyt-mcp_search is called with tool_name cyt-mcp_search
    Then search should fail with self-lookup error

  Scenario: Search rejects tool_name not in enum
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When cyt-mcp_search is called with tool_name codebase-memory-mcp_missing_tool
    Then search should fail with unknown tool error

  Scenario: Catalog export excludes cyt-mcp_search and never seeds it
    Given a runtime cache with only backend tool codebase-memory-mcp_query_graph
    When catalog payload is exported for agent cursor
    Then catalog tool names should not include cyt-mcp_search
    And catalog tool names should include codebase-memory-mcp_query_graph

  Scenario: cyt-mcp_search stub is never pruned like backend stubs
    Given cyt-mcp_search tool and backend tool codebase-memory-mcp_search_graph
    When stub list transform runs for agent cursor without descriptions
    Then cyt-mcp_search stub should retain tool_name enum schema and description
    And backend stub should have minimal empty inputSchema
    And cyt-mcp_search should not be stored in runtime catalog cache

  Scenario: cyt-mcp search CLI prints JSON with --json
    Given a runtime cache with tool codebase-memory-mcp_search_graph
    When cyt-mcp search CLI is run with --json for codebase-memory-mcp_search_graph
    Then CLI stdout should be valid JSON with full inputSchema
