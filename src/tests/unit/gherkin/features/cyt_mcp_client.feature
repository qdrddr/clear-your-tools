Feature: cyt-mcp cyt-client gate and pairing
  Pre-toolcall validation and session-scoped config pairing.
  Mirrors plan §7 tool_gate and Pairing tests.

  Scenario Outline: MCP tool names normalize to catalog names
    Given agent <agent>
    When MCP tool name <raw_name> is normalized
    Then normalized name should be <catalog_name>

    Examples:
      | agent  | raw_name                      | catalog_name          |
      | codex  | mcp__filesystem__read_file    | filesystem_read_file  |
      | cursor | MCP:filesystem_read_file      | filesystem_read_file  |
      | claude | filesystem_read_file          | filesystem_read_file  |

  Scenario: Pre-tool gate allows a tool injected in the session log
    Given a session log with cyt-mcp tool filesystem_read_file
    And a preToolUse payload calling filesystem_read_file with path /tmp/x
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: Pre-tool gate denies a tool not in the session log
    Given a session log with cyt-mcp tool filesystem_read_file
    And a preToolUse payload calling filesystem_write_file
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pre-tool gate denies unknown tool properties
    Given a session log with cyt-mcp tool filesystem_read_file
    And a preToolUse payload with an unknown property on filesystem_read_file
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pre-tool gate denies invalid enum values
    Given a session log with cyt-mcp tool demo_enum_tool
    And a preToolUse payload with an invalid enum value
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pairing repairs cyt-mcp MCP entry on sessionStart
    Given cyt_mcp is enabled in user config
    And cursor MCP config has no cyt-mcp entry
    When cyt-client handles sessionStart
    Then cursor mcp.json should contain a cyt-mcp server entry

  Scenario: Pairing skips when cyt_mcp is not in tools_from
    Given cyt_mcp is not enabled in user config
    And cursor MCP config has no cyt-mcp entry
    When cyt-client handles sessionStart
    Then cursor mcp.json should not be modified

  Scenario: Pairing does not run on UserPromptSubmit
    Given cyt_mcp is enabled in user config
    And cursor MCP config has no cyt-mcp entry
    When cyt-client handles UserPromptSubmit
    Then cursor mcp.json should not be modified
