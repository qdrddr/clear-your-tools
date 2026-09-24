Feature: Fish-safe hook wrappers and corrupt MCP config recovery
  Cursor hooks must run under fish or bash via shell wrapper scripts, and corrupt
  workspace mcp-config.yaml must be removed so setup can regenerate valid YAML.

  Scenario: Cursor hook install uses fish-safe shell wrappers
    Given cyt hook development mode for the current repo
    When cursor hook entries are built with shell wrappers
    Then hook commands should point at shell wrapper scripts
    And hook commands should not use inline CYT_WORKSPACE env prefixes
    And the client wrapper script should resolve workspace from Cursor env vars

  Scenario: Corrupt workspace mcp-config is backed up and removed during canonicalization
    Given a consumer workspace with corrupt workspace mcp-config
    When workspace MCP canonicalization runs
    Then the corrupt mcp-config should be backed up and removed

  Scenario: Workspace MCP aggregator rewrite produces valid YAML after corrupt removal
    Given a consumer workspace with corrupt workspace mcp-config
    When workspace MCP canonicalization runs
    And workspace MCP aggregator is rewritten for cursor
    Then workspace mcp-config should be valid YAML
