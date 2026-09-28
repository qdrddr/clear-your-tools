Feature: Tool schema completeness for cyt_mcp Type-2 gate
  Guard against partial input_schema stubs (query-only semble_search, empty {}) breaking
  preToolUse validation and backend calls that require repo.

  Background:
    Given agent cursor

  Scenario: Type-2 semble_search with full schema allows query and repo
    Given a Type-2 cyt_mcp catalog with tool semble_search query string repo string required
    When preToolUse validates cyt-mcp tool semble_search with args query bm25 repo /tmp/repo
    Then validation should allow

  Scenario: Type-2 semble_search with full schema denies missing repo
    Given a Type-2 cyt_mcp catalog with tool semble_search query string repo string required
    When preToolUse validates cyt-mcp tool semble_search with args query bm25
    Then validation should deny

  Scenario: Type-2 semble_search with empty schema denies all arguments
    Given a Type-2 cyt_mcp catalog with tool semble_search empty schema
    When preToolUse validates cyt-mcp tool semble_search with args query bm25 repo /tmp/repo
    Then validation should deny

  Scenario: Type-2 fff_grep query-only schema allows query without repo
    Given a Type-2 cyt_mcp catalog with tool fff_grep query string required
    When preToolUse validates cyt-mcp tool fff_grep with args query bm25
    Then validation should allow
