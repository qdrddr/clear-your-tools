Feature: cyt-mcp aggregator
  Standalone FastMCP aggregator: stub projection, catalog export, fault isolation.
  Mirrors plan §7 cyt-mcp and stdio catalog tests.

  Scenario: Stub list exposes minimal object inputSchema
    Given a cached FastMCP tool named filesystem_read_file with a full schema
    When stub list transform runs without descriptions
    Then stub inputSchema should be a minimal empty object schema
    And catalog export should preserve the full tool name filesystem_read_file

  Scenario: Catalog JSON names match stub cache names
    Given a runtime cache with tools filesystem_read_file and context7_query
    When catalog payload is exported for agent cursor
    Then catalog tool names should equal cache tool names exactly

  Scenario: Per-server mount failure marks backend degraded without aborting
    Given mcp server configs with one valid and one invalid backend
    When backend servers are mounted on the aggregator
    Then degraded servers should include broken_backend
    And the aggregator server should still be constructed
