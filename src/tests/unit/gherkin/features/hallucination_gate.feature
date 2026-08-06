Feature: Verify-only hallucination gate

  Background:
    Given agent cursor

  Scenario: Verify-only session gates unknown cyt-mcp tool when Type-2 catalog present
    Given a verify-only session log with Type-2 cyt_mcp catalog tool filesystem_read_file path string
    When preToolUse validates cyt-mcp tool filesystem_write_file with args path /tmp/x
    Then validation should deny
    And deny reason should not mention get-tool-definitions

  Scenario: Verify-only session allows matching cyt-mcp tool
    Given a verify-only session log with Type-2 cyt_mcp catalog tool filesystem_read_file path string
    When preToolUse validates cyt-mcp tool filesystem_read_file with args path /tmp/x
    Then validation should allow

  Scenario: Verify-only explicit branch activates gate when tools inject disabled
    Given a verify-only session log with tools inject disabled and Type-2 cyt_mcp catalog tool demo_tool path string
    When preToolUse validates cyt-mcp tool demo_tool with args bogus x
    Then validation should deny

  Scenario: Skills-only without hallucination gate flag still allows without catalog
    Given a session log with tools inject disabled
    When preToolUse validates cyt-mcp tool codebase-memory-mcp_query_graph with args project demo query MATCH
    Then validation should allow

  Scenario: Type-1 tool bulk dedup skips duplicate tool hashes
    Given a session jsonl with Type-1 cyt_mcp tool demo_tool hash abc111
    When appending Type-1 cyt_mcp tool demo_tool hash abc111
    Then session jsonl should have 1 tool lines

  Scenario: Hook connect verify-only response flag is authoritative
    Given verify-only hook connect response with verify-only true
    Then hook response is verify-only mode
