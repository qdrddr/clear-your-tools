Feature: cyt-mcp frontend install gate
  cyt-mcp-usr / cyt-mcp-ws should be installed only when agent MCP config or cyt
  backend defs already contain at least one non-frontend MCP server.

  Scenario Outline: User-scoped frontend install gate (<agent>)
    Given cyt hook development mode for the current repo
    And user-scoped <agent> MCP install state is "<agent_mcp_state>" with defs "<defs_state>"
    When cyt-mcp user setup runs for <agent>
    Then user-scoped <agent> should_install cyt-mcp-usr is "<expect_should_install>"

  Examples:
    | agent  | agent_mcp_state | defs_state  | expect_should_install |
    | cursor | missing         | missing     | false                 |
    | cursor | has_backend     | empty       | true                  |
    | cursor | missing         | has_backend | true                  |
    | claude | missing         | missing     | false                 |
    | claude | has_backend     | empty       | true                  |
    | codex  | missing         | has_backend | true                  |

  Scenario Outline: Workspace-scoped frontend install gate (<agent>)
    Given cyt hook development mode for the current repo
    And workspace-scoped <agent> MCP install state is "<agent_mcp_state>" with defs "<defs_state>"
    When cyt-mcp workspace setup runs for <agent>
    Then workspace-scoped <agent> should_install cyt-mcp-ws is "<expect_should_install>"

  Examples:
    | agent  | agent_mcp_state | defs_state  | expect_should_install |
    | cursor | missing         | missing     | false                 |
    | cursor | has_backend     | empty       | true                  |
    | cursor | missing         | has_backend | true                  |
    | claude | empty           | has_backend | true                  |
    | codex  | has_backend     | empty       | true                  |
