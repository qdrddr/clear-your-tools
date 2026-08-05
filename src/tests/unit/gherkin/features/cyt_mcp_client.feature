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

  Scenario Outline: get-tool-definitions normalizes across agents
    Given agent <agent>
    When MCP tool name <raw_name> is normalized
    Then normalized name should be cyt-mcp_get-tool-definitions

    Examples:
      | agent  | raw_name                                  |
      | codex  | mcp__cyt-mcp__get-tool-definitions        |
      | cursor | MCP:get-tool-definitions                  |
      | cursor | MCP:cyt-mcp_get-tool-definitions          |
      | claude | cyt-mcp_get-tool-definitions              |

  Scenario: Pre-tool gate allows get-tool-definitions with tool_name and no session log
    Given no session log file
    And a preToolUse payload calling get-tool-definitions with tool_name codebase-memory-mcp_search_graph
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: Pre-tool gate allows get-tool-definitions with tool_name and empty session log
    Given an empty session log
    And a preToolUse payload calling get-tool-definitions with tool_name codebase-memory-mcp_search_graph
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: Pre-tool gate denies get-tool-definitions without tool_name
    Given no session log file
    And a preToolUse payload calling get-tool-definitions without tool_name
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pre-tool gate denies get-tool-definitions with empty tool_name
    Given no session log file
    And a preToolUse payload calling get-tool-definitions with empty tool_name
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pre-tool gate allows cyt-mcp backend when no session log file
    Given no session log file
    And a preToolUse payload calling codebase-memory-mcp_query_graph
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: Pre-tool gate allows cyt-mcp backend when session log has turn only
    Given a session log with turn entry only
    And a preToolUse payload calling codebase-memory-mcp_query_graph
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: non-resolved backend cyt_mcp tool remains denied when tools inject active
    Given a session log with tools inject enabled and no Type-2 catalog
    And a preToolUse payload calling codebase-memory-mcp_query_graph
    When pre-tool validation runs
    Then pre-tool validation should deny the call

  Scenario: Pre-tool gate allows non-cyt-mcp tools when no session log file
    Given no session log file
    And a preToolUse payload calling Shell
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario: get-tool-definitions-resolved tool entry unlocks tool gate
    Given a session log with only a get-tool-definitions-resolved cyt_mcp tool entry
    And a preToolUse payload calling codebase-memory-mcp_search_graph
    When pre-tool validation runs
    Then pre-tool validation should allow the call

  Scenario Outline: post-tool hook persists get-tool-definitions result into cyt_mcp catalog
    Given agent <agent>
    And an empty session log
    And a <hook_event> payload for get-tool-definitions with tool_name codebase-memory-mcp_search_graph
    When cyt-client handles post-tool capture
    Then session log should contain a tool entry for codebase-memory-mcp_search_graph
    And tool entry catalog should be cyt_mcp
    And tool entry full should be true
    And tool entry source should be cyt-mcp_get-tool-definitions

    Examples:
      | agent  | hook_event        |
      | cursor | postToolUse       |
      | claude | PostToolUse       |
      | codex  | PostToolUse       |

  Scenario: beforeSubmitPrompt persists turn with prompt and assistant
    Given an empty session log
    And a beforeSubmitPrompt payload with prompt and transcript
    When cyt-client persists turn to session log
    Then session log should contain a turn entry with matching prompt

  Scenario: combined session text includes turn corpus for pre-exposed gate
    Given a session log with a turn entry and a full cyt_mcp tool entry
    When combined session text is built
    Then corpus should include turn prompt text
