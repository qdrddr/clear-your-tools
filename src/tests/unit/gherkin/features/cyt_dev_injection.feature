Feature: cyt development mode injection
  When cyt hook runs via uv from a repo checkout, agent hook and MCP configs
  should use uv run --directory with scripts relative to that repo.

  Scenario: Dev hook setup installs uv run cyt-client commands
    Given cyt hook development mode for the current repo
    When cursor hook entries are built for development mode
    Then the cyt-client hook command should use uv run from the repo root
    And the daemon start hook command should use uv run from the repo root

  Scenario: Dev cyt-mcp setup installs uv run aggregator command
    Given cyt hook development mode for the current repo
    When cyt-mcp cursor MCP entry is written for development mode
    Then cursor mcp.json cyt-mcp entry should use uv run from the repo root

  Scenario: Dev pairing repairs MCP entry from development hooks
    Given cyt hook development mode for the current repo
    And cursor hooks.json contains development cyt-client commands
    And cursor MCP config has no cyt-mcp entry
    When cyt-client pairing repairs MCP config
    Then cursor mcp.json cyt-mcp entry should use uv run from the repo root

  Scenario: MCP migration excludes cyt-mcp frontend from backends
    Given cyt hook development mode for the current repo
    And cursor mcp.json contains cyt-mcp frontend and a backend server
    When backend MCP servers are migrated for cursor
    Then migrated backends should exclude cyt-mcp frontend
    And migrated backends should include the backend server

  Scenario: MCP migration leaves only cyt-mcp in agent mcp.json
    Given cyt hook development mode for the current repo
    And cursor mcp.json contains cyt-mcp frontend and a backend server
    When cyt-mcp setup migrates backends for cursor
    Then cursor agent mcp.json should contain only cyt-mcp
    And migrated backends should include the backend server
