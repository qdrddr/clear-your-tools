Feature: cyt-mcp workspace server defs contract
  `.agents/cyt/config/mcp/cursor.json` must remain strict JSON with an mcpServers object.
  YAML in that path breaks Cursor MCP startup; setup/migration must always write JSON.

  Scenario: Workspace MCP migration writes JSON server defs
    Given a git workspace with backend MCP servers in .cursor/mcp.json
    When cyt-mcp workspace setup migrates backends for cursor
    Then workspace server defs at .agents/cyt/config/mcp/cursor.json should be valid JSON

  Scenario: Valid JSON workspace server defs load aggregator backends
    Given a workspace with valid JSON MCP server defs
    When workspace aggregator config is loaded for cursor
    Then loaded MCP server keys should include configured backend servers

  Scenario: Legacy YAML cursor.json still loads via defensive fallback
    Given a workspace with YAML MCP server defs stored at cursor.json
    When workspace aggregator config is loaded for cursor
    Then loaded MCP server keys should include configured backend servers
