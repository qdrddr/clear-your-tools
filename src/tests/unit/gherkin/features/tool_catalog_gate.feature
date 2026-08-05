Feature: Type-2 tool catalog hallucination gate

  Background:
    Given agent cursor

  Scenario: Skills-only session allows preToolUse without Type-2 catalog
    Given a session log with tools inject disabled
    When preToolUse validates cyt-mcp tool codebase-memory-mcp_query_graph with args project demo query MATCH
    Then validation should allow

  Scenario: Type-2 cyt_mcp catalog allows matching MCP call
    Given a Type-2 cyt_mcp catalog with tool filesystem_read_file path string
    When preToolUse validates cyt-mcp tool filesystem_read_file with args path /tmp/x
    Then validation should allow

  Scenario: Type-2 cyt_mcp catalog denies unknown tool
    Given a Type-2 cyt_mcp catalog with tool filesystem_read_file path string
    When preToolUse validates cyt-mcp tool filesystem_write_file with args path /tmp/x
    Then validation should deny

  Scenario: Type-2 cyt_mcp catalog denies schema violation
    Given a Type-2 cyt_mcp catalog with tool filesystem_read_file path string required
    When preToolUse validates cyt-mcp tool filesystem_read_file with args bogus x
    Then validation should deny

  Scenario: Type-2 mcpc Shell command validates against mcpc catalog
    Given a Type-2 mcpc catalog session @ctx7 tool resolve-library-id libraryName string query string
    When preToolUse validates Shell command mcpc_shell_resolve_library_id
    Then validation should allow

  Scenario: Type-2 mcpc Shell denies tool not in catalog
    Given a Type-2 mcpc catalog session @ctx7 tool resolve-library-id libraryName string
    When preToolUse validates Shell command mcpc_shell_unknown_tool
    Then validation should deny

  Scenario: Cross-source isolation mcpc Shell is not validated against cyt_mcp catalog
    Given a Type-2 cyt_mcp catalog with tool filesystem_read_file path string
    When preToolUse validates Shell command mcpc_shell_read_file
    Then validation should allow

  Scenario: Missing session log or inject flag without Type-2 catalog allows cyt-mcp backend call
    Given a session log with tools inject enabled and no Type-2 catalog
    When preToolUse validates cyt-mcp tool codebase-memory-mcp_query_graph with args project demo query MATCH
    Then validation should allow

  Scenario: Missing session jsonl file allows cyt-mcp backend call
    Given no session log file for preToolUse
    When preToolUse validates cyt-mcp tool codebase-memory-mcp_query_graph with args project demo query MATCH
    Then validation should allow

  Scenario: get-tool-definitions requires tool_name argument
    When preToolUse validates cyt-mcp get-tool-definitions with empty args
    Then validation should deny

  Scenario: Client persists tool_catalog with hash dedup
    Given a Type-2 cyt_mcp catalog entry on disk hash abc123
    When appending the same Type-2 cyt_mcp catalog entry hash abc123
    Then session jsonl should have 1 tool_catalog lines

  Scenario: Session log POST excludes full tool_catalog bodies
    Given a session jsonl with Type-2 cyt_mcp catalog tool demo_tool
    When building hook POST session log payload
    Then cyt_session_log should not contain tool_catalog kind
    And tool_catalog_hashes should include tool_catalog:cyt_mcp
